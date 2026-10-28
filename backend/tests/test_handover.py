import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import CrewMember
from silencio_set.tasks import repository as module
from silencio_set.tasks.repository import TaskActor, TaskRepository
from silencio_set.tasks.schemas import ExitResponsibilityPage
from silencio_set.tasks.service import TaskService
from test_account_repository import fake_session_factory
from test_auth_api import auth_client
from test_crews import crew_client
from test_tasks import task_row


def test_handover_counts_hidden_tasks_without_leaking_titles_or_identifiers(monkeypatch):
    factory, session, _ = fake_session_factory(); target = CrewMember(id=uuid4(), crew_id=uuid4(), status="active", lock_version=2)
    actor = TaskActor(uuid4(), frozenset({"member.remove", "task.read"}))
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=actor))
    task = task_row(crew_id=target.crew_id, owner_member_id=target.id)
    session.scalar.side_effect = [target, 3, 1]
    session.scalars.return_value = Mock(all=Mock(return_value=[task]))
    result = asyncio.run(TaskRepository(factory).responsibilities(uuid4(), target.crew_id, target.id, 1, 20))
    assert result.blocking_total == 3 and result.restricted_total == 2 and result.total == 1
    assert len(result.items) == 1 and result.items[0].id == task.id and result.items[0].is_owner
    assert module.task_actor.call_args.kwargs["writing"] is True
    assert "LIMIT" in str(session.scalars.call_args.args[0])


def test_member_without_task_read_still_gets_blocker_count_but_no_task_data(monkeypatch):
    factory, session, _ = fake_session_factory(); target = CrewMember(id=uuid4(), crew_id=uuid4(), status="active", lock_version=1)
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=TaskActor(target.id, frozenset())))
    session.scalar.side_effect = [target, 2]
    result = asyncio.run(TaskRepository(factory).responsibilities(uuid4(), target.crew_id, target.id, 1, 20))
    assert result.restricted_total == 2 and result.items == []
    session.scalars.assert_not_awaited()


def test_members_cannot_inspect_other_members_exit_responsibilities(monkeypatch):
    factory, session, _ = fake_session_factory()
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=TaskActor(uuid4(), frozenset({"task.read"}))))
    with pytest.raises(AuthError) as caught:
        asyncio.run(TaskRepository(factory).responsibilities(uuid4(), uuid4(), uuid4(), 1, 20))
    assert caught.value.code == "FORBIDDEN"
    session.scalar.assert_not_awaited()


def test_cross_crew_or_inactive_member_is_hidden(monkeypatch):
    factory, session, _ = fake_session_factory()
    monkeypatch.setattr(module, "task_actor", AsyncMock(return_value=TaskActor(uuid4(), frozenset({"member.remove"}))))
    session.scalar.return_value = None
    with pytest.raises(AuthError) as caught:
        asyncio.run(TaskRepository(factory).responsibilities(uuid4(), uuid4(), uuid4(), 1, 20))
    assert caught.value.status == 404
    query = str(session.scalar.call_args.args[0])
    assert "crew_members.crew_id =" in query and "crew_members.status =" in query


def test_handover_api_uses_authenticated_user_and_bounds_pagination(crew_client):
    client, audits, user, crew = crew_client
    result = ExitResponsibilityPage(member_id=crew.membership.id, member_lock_version=1, items=[],
        total=0, blocking_total=0, restricted_total=0, page=1, page_size=20)
    repo = Mock(responsibilities=AsyncMock(return_value=result))
    client.app.state.tasks = TaskService(repo, audits)
    url = f"/api/v1/crews/{crew.id}/members/{crew.membership.id}/exit-responsibilities"
    assert client.get(url).status_code == 200
    assert repo.responsibilities.call_args.args[:2] == (user.id, crew.id)
    assert repo.responsibilities.call_args.kwargs["member_id"] == crew.membership.id
    assert client.get(url + "?page_size=101").status_code == 422
