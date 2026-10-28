import asyncio
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.crews.models import AuditLog
from silencio_set.tasks import repository as module
from silencio_set.tasks.models import Task, TaskSubmission, TaskReceipt, TaskChangeEvent
from silencio_set.tasks.repository import TaskActor, TaskRepository, allowed_actions, check_action, blocking_tasks_query, check_task_responsibilities, request_digest, task_view, visible_tasks
from silencio_set.tasks.schemas import TaskCreate, TaskPatch, VersionInput, SubmissionInput, ReviewInput, CancelInput, TaskPage
from silencio_set.tasks.service import TaskService
from silencio_set.infrastructure.database import Base
from test_account_repository import fake_session_factory
from test_crews import crew_client, ready_user
from test_auth_api import auth_client


def task_row(**changes):
    now = datetime.now(UTC)
    return Task(**({"id": uuid4(), "crew_id": uuid4(), "title": "准备分镜", "description": None,
        "owner_member_id": uuid4(), "reviewer_member_id": uuid4(), "created_by_member_id": uuid4(),
        "status": "pending", "due_at": now + timedelta(days=1), "lock_version": 1,
        "created_at": now, "updated_at": now, "cancel_reason": None} | changes))


def create_body(task):
    return TaskCreate(title=task.title, owner_member_id=task.owner_member_id,
        reviewer_member_id=task.reviewer_member_id, due_at=task.due_at)


@pytest.mark.parametrize("field,value", [("title", " "), ("title", "x" * 101), ("title", "a\nb"),
    ("due_at", "2026-11-01T10:00:00"), ("due_at", 1_900_000_000), ("due_at", "1900000000"),
    ("scene_id", str(uuid4())), ("status", "completed"), ("description", "x" * 3001)])
def test_task_creation_rejects_invalid_fields_or_fabricated_scene(field, value):
    body = create_body(task_row()).model_dump() | {field: value}
    with pytest.raises(ValidationError): TaskCreate(**body)


@pytest.mark.parametrize("changes", [{}, {"status": "completed"}, {"owner_member_id": None}, {"due_at": None}, {"collaborator_member_ids": [str(uuid4())] * 2}])
def test_patch_cannot_change_state_or_clear_required_assignments(changes):
    with pytest.raises(ValidationError): TaskPatch(expected_lock_version=1, **changes)


def test_notes_versions_and_review_decisions_follow_contract():
    for value in (True, "1", 0, 2_147_483_648):
        with pytest.raises(ValidationError): VersionInput(expected_lock_version=value)
    with pytest.raises(ValidationError): ReviewInput(expected_lock_version=1, submission_id=uuid4(), decision="reject")
    with pytest.raises(ValidationError): ReviewInput(expected_lock_version=1, submission_id=uuid4(), decision="approve")
    assert SubmissionInput(expected_lock_version=1, completion_note="  第一稿\n附说明 ").completion_note == "第一稿\n附说明"
    assert TaskPatch(expected_lock_version=1, description=None).model_dump(exclude_unset=True)["description"] is None


def test_manager_cannot_submit_or_review_for_the_assigned_members():
    row = task_row(status="in_progress")
    actor = TaskActor(uuid4(), frozenset({"task.read", "task.manage", "task.work", "task.review"}))
    assert allowed_actions(row, actor) == ["cancel", "edit"]
    for action in ("submit", "review"):
        with pytest.raises(AuthError) as caught: check_action(row, actor, action, 1)
        assert caught.value.code == "FORBIDDEN"


@pytest.mark.parametrize("status,action,expected", [("pending", "start", True), ("in_progress", "submit", True),
    ("pending", "submit", False), ("completed", "start", False), ("pending_review", "submit", False)])
def test_owner_actions_follow_state_machine(status, action, expected):
    row = task_row(status=status)
    actor = TaskActor(row.owner_member_id, frozenset({"task.work"}))
    if expected: check_action(row, actor, action, 1)
    else:
        with pytest.raises(AuthError) as caught: check_action(row, actor, action, 1)
        assert caught.value.code == "INVALID_STATE"


def test_explicit_permission_denial_stale_version_and_exhaustion_block_writes():
    row = task_row()
    with pytest.raises(AuthError): check_action(row, TaskActor(row.owner_member_id, frozenset()), "start", 1)
    actor = TaskActor(row.owner_member_id, frozenset({"task.work"}))
    with pytest.raises(AuthError) as caught: check_action(row, actor, "start", 2)
    assert caught.value.code == "VERSION_CONFLICT"
    row.lock_version = 2_147_483_647
    with pytest.raises(AuthError) as caught: check_action(row, actor, "start", row.lock_version)
    assert caught.value.code == "VERSION_EXHAUSTED"


def test_read_scope_is_same_crew_and_related_unless_manager():
    crew_id = uuid4(); actor = TaskActor(uuid4(), frozenset({"task.read"}))
    scoped = visible_tasks(crew_id, actor).compile(dialect=postgresql.dialect())
    assert "tasks.crew_id =" in str(scoped) and "task_collaborators" in str(scoped)
    assert crew_id in scoped.params.values() and actor.member_id in scoped.params.values()
    manager_sql = str(visible_tasks(crew_id, TaskActor(uuid4(), frozenset({"task.manage"}))))
    assert "task_collaborators" not in manager_sql and "tasks.crew_id =" in manager_sql


def test_task_responsibilities_only_block_nonterminal_owner_or_reviewer():
    compiled = blocking_tasks_query(uuid4(), uuid4()).compile(dialect=postgresql.dialect())
    assert "owner_member_id" in str(compiled) and "reviewer_member_id" in str(compiled)
    assert "task_collaborators" not in str(compiled)
    assert set(next(value for value in compiled.params.values() if isinstance(value, list))) == {"completed", "cancelled"}
    _, session, _ = fake_session_factory()
    session.scalar.return_value = uuid4()
    with pytest.raises(AuthError) as caught: asyncio.run(check_task_responsibilities(session, uuid4(), uuid4()))
    assert caught.value.code == "MEMBER_HAS_RESPONSIBILITIES"
    session.scalar.return_value = None
    asyncio.run(check_task_responsibilities(session, uuid4(), uuid4()))


def mutation_fixture(monkeypatch, action, *, status="pending", current=None):
    factory, session, transaction = fake_session_factory()
    row = task_row(status=status)
    actor_id = row.reviewer_member_id if action == "review" else row.owner_member_id
    actor = TaskActor(actor_id, frozenset({"task.read", "task.work", "task.review", "task.manage"}))
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(module, "collaborators_for", AsyncMock(return_value={row.id: []}))
    monkeypatch.setattr(module, "validate_assignment", AsyncMock())
    now = datetime.now(UTC)
    keyed = action in {"create", "submit", "review", "cancel"}
    session.scalar.side_effect = ([] if action == "create" else [row]) + ([None] if keyed else []) + [now] + ([2] if action == "submit" else [current] if action in {"review", "cancel"} else [])
    return factory, session, transaction, row, actor


def mutate(factory, row, action, body, *, key="test-only-key"):
    return asyncio.run(TaskRepository(factory).mutate(uuid4(), row.crew_id, action, body, "a" * 32,
        task_id=None if action == "create" else row.id, idempotency_key=key if action in {"create", "submit", "review", "cancel"} else None))


def test_create_saves_task_event_receipt_and_audit_together(monkeypatch):
    factory, session, transaction, row, _ = mutation_fixture(monkeypatch, "create")
    result = mutate(factory, row, "create", create_body(row))
    inserted = [call.args[0] for call in session.add.call_args_list]
    assert {type(item) for item in inserted} == {Task, TaskReceipt, TaskChangeEvent, AuditLog}
    assert result.status == "pending" and result.lock_version == 1
    assert all(item.crew_id == row.crew_id for item in inserted)
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


def test_start_moves_only_pending_task_and_records_no_fake_submission(monkeypatch):
    factory, session, _, row, _ = mutation_fixture(monkeypatch, "start")
    result = mutate(factory, row, "start", VersionInput(expected_lock_version=1))
    assert result.status == "in_progress" and result.lock_version == 2
    assert not any(isinstance(call.args[0], TaskSubmission) for call in session.add.call_args_list)


def test_resubmission_creates_new_number_without_overwriting_prior_history(monkeypatch):
    factory, session, _, row, _ = mutation_fixture(monkeypatch, "submit", status="in_progress")
    result = mutate(factory, row, "submit", SubmissionInput(expected_lock_version=1, completion_note="第二版"))
    submission = next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], TaskSubmission))
    assert submission.submission_no == 3 and submission.review_status == "pending"
    assert result.status == "pending_review" and submission.submitted_by == row.owner_member_id


@pytest.mark.parametrize("decision,status,review_status", [("accept", "completed", "accepted"), ("reject", "in_progress", "rejected")])
def test_review_updates_current_submission_and_task_as_one_change(monkeypatch, decision, status, review_status):
    submission = TaskSubmission(id=uuid4(), review_status="pending")
    factory, session, _, row, _ = mutation_fixture(monkeypatch, "review", status="pending_review", current=submission)
    result = mutate(factory, row, "review", ReviewInput(expected_lock_version=1, submission_id=submission.id, decision=decision, review_note="调整布光"))
    assert result.status == status and submission.review_status == review_status
    assert submission.reviewed_by == row.reviewer_member_id and submission.reviewed_at is not None


def test_review_of_old_submission_cannot_update_anything(monkeypatch):
    submission = TaskSubmission(id=uuid4(), review_status="pending")
    factory, session, _, row, _ = mutation_fixture(monkeypatch, "review", status="pending_review", current=submission)
    with pytest.raises(AuthError) as caught:
        mutate(factory, row, "review", ReviewInput(expected_lock_version=1, submission_id=uuid4(), decision="accept"))
    assert caught.value.code == "INVALID_STATE" and submission.review_status == "pending" and row.lock_version == 1
    session.add.assert_not_called()


def test_cancelling_pending_review_closes_submission_without_fake_rejection(monkeypatch):
    submission = TaskSubmission(id=uuid4(), review_status="pending")
    factory, _, _, row, _ = mutation_fixture(monkeypatch, "cancel", status="pending_review", current=submission)
    result = mutate(factory, row, "cancel", CancelInput(expected_lock_version=1, reason="拍摄方案调整"))
    assert result.status == "cancelled" and result.cancel_reason == "拍摄方案调整"
    assert submission.review_status == "cancelled" and submission.review_note is None


def test_reassignment_validates_active_members_and_notifies_both_old_and_new_responsible(monkeypatch):
    factory, session, _, row, _ = mutation_fixture(monkeypatch, "edit")
    old_owner = row.owner_member_id; new_owner = uuid4()
    result = mutate(factory, row, "edit", TaskPatch(expected_lock_version=1, owner_member_id=new_owner))
    assert result.owner_member_id == new_owner
    module.validate_assignment.assert_awaited_once()
    event = next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], TaskChangeEvent))
    assert str(old_owner) in event.recipient_member_ids and str(new_owner) in event.recipient_member_ids


def test_commit_failure_does_not_return_success(monkeypatch):
    factory, _, transaction, row, _ = mutation_fixture(monkeypatch, "start")
    transaction.__aexit__.side_effect = OperationalError("private SQL", {}, Exception("commit unknown"))
    with pytest.raises(OperationalError): mutate(factory, row, "start", VersionInput(expected_lock_version=1))


@pytest.mark.parametrize("changed", [False, True])
def test_same_key_restores_original_result_or_rejects_changed_content_before_version_check(monkeypatch, changed):
    factory, session, _, row, actor = mutation_fixture(monkeypatch, "submit", status="pending_review")
    original = row.lock_version
    body = SubmissionInput(expected_lock_version=1, completion_note="原提交")
    receipt = TaskReceipt(task_id=row.id, request_digest=request_digest("submit", row.id, body),
        response_snapshot=task_view(row, [], actor).model_dump(mode="json"))
    row.lock_version = 99
    session.scalar.side_effect = [row, receipt]
    if changed:
        with pytest.raises(AuthError) as caught: mutate(factory, row, "submit", body.model_copy(update={"completion_note": "其他内容"}))
        assert caught.value.code == "IDEMPOTENCY_KEY_REUSED"
    else:
        result = mutate(factory, row, "submit", body)
        assert result.lock_version == original and result.allowed_actions == []
    session.add.assert_not_called(); session.flush.assert_not_awaited()


def test_replay_requires_current_assignment_not_past_success(monkeypatch):
    factory, session, _, row, actor = mutation_fixture(monkeypatch, "submit", status="pending_review")
    body = SubmissionInput(expected_lock_version=1, completion_note="原提交")
    receipt = TaskReceipt(request_digest=request_digest("submit", row.id, body), response_snapshot={})
    row.owner_member_id = uuid4()
    session.scalar.side_effect = [row, receipt]
    with pytest.raises(AuthError) as caught: mutate(factory, row, "submit", body)
    assert caught.value.code == "FORBIDDEN"
    session.add.assert_not_called()


def configure_task_api(crew_client):
    client, audit, user, crew = crew_client
    row = task_row(crew_id=crew.id)
    view = task_view(row, [], TaskActor(row.owner_member_id, frozenset({"task.work"})))
    repo = Mock(mutate=AsyncMock(return_value=view), list=AsyncMock(return_value=TaskPage(items=[view], total=1, page=1, page_size=20)))
    client.app.state.tasks = TaskService(repo, audit)
    return client, repo, user, row


def test_http_create_binds_actor_and_uuid_key_and_does_not_echo_private_inputs(crew_client):
    client, repo, user, row = configure_task_api(crew_client); key = str(uuid4())
    response = client.post(f"/api/v1/crews/{row.crew_id}/tasks", json=create_body(row).model_dump(mode="json"), headers={"Idempotency-Key": key})
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    assert repo.mutate.call_args.args[0] == user.id and repo.mutate.call_args.kwargs["idempotency_key"] == key
    assert "email" not in response.text and "password" not in response.text


@pytest.mark.parametrize("header,value", [("Origin", "https://evil.example"), ("X-CSRF-Token", "bad"), ("Idempotency-Key", "bad")])
def test_task_write_rejects_bad_security_headers(crew_client, header, value):
    client, repo, _, row = configure_task_api(crew_client)
    response = client.post(f"/api/v1/crews/{row.crew_id}/tasks", json=create_body(row).model_dump(mode="json"), headers={"Idempotency-Key": str(uuid4()), header: value})
    assert response.status_code in {403, 422}
    repo.mutate.assert_not_awaited()


def test_required_key_and_bounded_queries_are_enforced(crew_client):
    client, repo, _, row = configure_task_api(crew_client)
    base = f"/api/v1/crews/{row.crew_id}/tasks"
    assert client.post(base, json=create_body(row).model_dump(mode="json")).status_code == 422
    for suffix in ("?status=bogus", "?page_size=101", "?due_from=2026-11-01T00:00:00", "?due_from=2026-11-02T00:00:00Z&due_to=2026-11-01T00:00:00Z"):
        assert client.get(base + suffix).status_code == 422
    repo.mutate.assert_not_awaited(); repo.list.assert_not_awaited()


def test_task_migration_offline_has_same_crew_fks_pending_uniqueness_and_receipt_scope(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://user:example@localhost:5432/test")
    monkeypatch.setattr(config_module, "load_settings", lambda: settings)
    output = StringIO(); command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()
    assert "0006_tasks" in sql and "CREATE TABLE task_action_receipts" in sql
    assert "FOREIGN KEY(crew_id, owner_member_id) REFERENCES crew_members (crew_id, id)" in sql
    assert "UNIQUE (crew_id, user_id, key_digest)" in sql
    assert "CREATE UNIQUE INDEX uq_task_submissions_current_pending" in sql
    assert "reviewed_at IS NOT NULL" in sql and "cancel_reason IS NOT NULL" in sql


def test_task_constraint_names_are_unique_within_each_table():
    for name in ("tasks", "task_submissions", "task_collaborators", "task_action_receipts", "task_change_events"):
        names = [constraint.name for constraint in Base.metadata.tables[name].constraints]
        assert len(names) == len(set(names))


def test_assignment_checks_same_crew_active_accounts_and_required_capabilities(monkeypatch):
    from silencio_set.crews.models import CrewMember
    _, session, _ = fake_session_factory()
    crew_id, owner, reviewer = uuid4(), uuid4(), uuid4()
    session.scalars.return_value = Mock(all=Mock(return_value=[]))
    with pytest.raises(AuthError) as caught: asyncio.run(module.validate_assignment(session, crew_id, owner, reviewer, []))
    assert caught.value.code == "MEMBER_INACTIVE"
    query = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    assert crew_id in query.params.values() and "users.status" in str(query) and "crew_members.status" in str(query)
    people = [CrewMember(id=owner, crew_id=crew_id), CrewMember(id=reviewer, crew_id=crew_id)]
    session.scalars.return_value = Mock(all=Mock(return_value=people))
    permissions = {owner: frozenset({"task.work", "task.read"}), reviewer: frozenset()}
    monkeypatch.setattr(module, "permissions_for", AsyncMock(return_value=permissions))
    with pytest.raises(AuthError) as caught: asyncio.run(module.validate_assignment(session, crew_id, owner, reviewer, []))
    assert caught.value.code == "MEMBER_NOT_ELIGIBLE"
    permissions[reviewer] = frozenset({"task.review", "task.read"})
    asyncio.run(module.validate_assignment(session, crew_id, owner, reviewer, []))


def test_task_detail_exposes_only_scoped_member_names_and_bounded_current_submission(monkeypatch):
    factory, session, _ = fake_session_factory(); row = task_row()
    actor = TaskActor(row.owner_member_id, frozenset({"task.read", "task.work"}))
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=actor))
    monkeypatch.setattr(module, "collaborators_for", AsyncMock(return_value={row.id: []}))
    session.scalar.side_effect = [row, 0, None]
    session.execute.return_value = Mock(all=Mock(return_value=[(row.owner_member_id, "摄影"), (row.reviewer_member_id, "导演")]))
    result = asyncio.run(TaskRepository(factory).get(uuid4(), row.crew_id, row.id))
    assert result.member_names[row.owner_member_id] == "摄影" and result.current_submission is None
    assert "email" not in result.model_dump_json()
    names_query = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "crew_members.crew_id =" in str(names_query) and row.crew_id in names_query.params.values()
    assert "LIMIT" in str(session.scalar.call_args.args[0])


def test_database_failure_returns_safe_error_and_failed_audit(caplog):
    repo = Mock(mutate=AsyncMock(side_effect=OperationalError("private SQL", {}, Exception("private password"))))
    audit = Mock(record_failure=AsyncMock())
    with pytest.raises(AuthError) as caught:
        asyncio.run(TaskService(repo, audit).execute(ready_user(), "cancel", "a" * 32, crew_id=uuid4(), task_id=uuid4(), body=CancelInput(expected_lock_version=1, reason="private reason"), idempotency_key="test"))
    assert caught.value.code == "SERVICE_UNAVAILABLE" and "private" not in caplog.text
    assert audit.record_failure.call_args.args[3] == "failed"


def test_application_wires_real_task_exit_guard_without_connecting_to_dependencies():
    from fastapi.testclient import TestClient
    from silencio_set.main import create_app
    settings = Settings(_env_file=None, app_env="test", auth_enabled=True, auth_secret="synthetic-test-secret-" * 3,
        database_url="postgresql+asyncpg://user:example@127.0.0.1:5432/test", redis_url="redis://127.0.0.1:6379/15")
    with TestClient(create_app(settings)) as client:
        assert module.check_task_responsibilities in client.app.state.crews.organization.exit_checks
        assert isinstance(client.app.state.tasks.repository, TaskRepository)
