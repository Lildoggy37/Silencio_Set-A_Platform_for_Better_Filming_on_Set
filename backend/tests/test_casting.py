import asyncio
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.creative import casting as module
from silencio_set.creative.casting_schemas import CastingSave, CastingPage, CastingEntry, ActorPage
from silencio_set.creative.models import CharacterCasting, CreativeReceipt, SceneCharacter, SceneRevision
from silencio_set.creative.repository import CreativeRepository
from silencio_set.creative.service import CreativeService
from silencio_set.crews.models import AuditLog, CrewMember, MemberRole
from silencio_set.plans import casting as plan_casting
from silencio_set.plans import dependencies
from silencio_set.plans.models import PlanVersion
from silencio_set.plans.publication import snapshot_digest
from silencio_set.resources.repository import ResourceActor
from silencio_set.scripts.models import Script, ScriptVersion
from test_account_repository import fake_session_factory
from test_plans import ZERO, at, context, codes
from test_auth_api import auth_client
from test_crews import crew_client


def setup(monkeypatch):
    factory, session, tx = fake_session_factory()
    crew, user, actor_id = uuid4(), uuid4(), uuid4()
    actor = ResourceActor(actor_id, frozenset({'member.read', 'script.read', 'scene.read', 'scene.review'}), frozenset({'director'}), ZERO)
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=actor))
    monkeypatch.setattr(module, 'mark_plan_review', AsyncMock())
    script = Script(id=uuid4(), crew_id=crew, lock_version=1, current_version_id=uuid4(), working_revision=1, title='雨停之前')
    member = CrewMember(id=uuid4(), crew_id=crew, user_id=uuid4(), display_name='陈同学', status='active', joined_at=ZERO)
    receipt = []
    binding = []
    async def scalar(query):
        entity = query.column_descriptions[0].get('entity')
        return {CreativeReceipt: receipt[0] if receipt else None, Script: script,
                CharacterCasting: binding[0] if binding else None, CrewMember: member, SceneCharacter: uuid4()}.get(entity, at(1))
    session.scalar.side_effect = scalar
    session.scalars.return_value = Mock(all=Mock(return_value=[]))
    repo = CreativeRepository(factory)
    body = CastingSave(expected_lock_version=1, character_name=' 林雨 ', member_id=member.id, membership_joined_at=ZERO)
    return NS(repo=repo, session=session, tx=tx, script=script, member=member, actor=actor, user=user, body=body, receipts=receipt, bindings=binding)


def save(v, body=None):
    return asyncio.run(module.save(v.repo, v.user, v.script.crew_id, body or v.body, 'a'*32,
                                  object_id=v.script.id, idempotency_key='original-key'))


def test_save_and_source_invalidation_audit_receipt_are_one_transaction(monkeypatch):
    v = setup(monkeypatch)
    result = save(v)
    saved = [call.args[0] for call in v.session.add.call_args_list]
    binding = next(row for row in saved if isinstance(row, CharacterCasting))
    assert binding.character_name == '林雨' and binding.member_id == v.member.id and binding.membership_joined_at == ZERO
    assert result.lock_version == v.script.lock_version == 2
    assert any(isinstance(row, AuditLog) for row in saved) and any(isinstance(row, CreativeReceipt) for row in saved)
    module.mark_plan_review.assert_awaited_once_with(v.session, v.script.crew_id, 'casting_changed', now=at(1), script_id=v.script.id, character_name='林雨')
    v.tx.__aexit__.assert_awaited_once_with(None, None, None)


def test_producer_can_bind_without_receiving_script_edit_permissions(monkeypatch):
    v = setup(monkeypatch)
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=replace(v.actor, permissions=frozenset({'plan.edit', 'member.read'}))))
    assert save(v).member_id == v.member.id


@pytest.mark.parametrize('permissions', [{'script.read', 'scene.read', 'scene.edit', 'member.read'}, {'member.read'}, {'plan.edit'}])
def test_writer_admin_or_missing_member_permission_cannot_bind(monkeypatch, permissions):
    v = setup(monkeypatch)
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=replace(v.actor, permissions=frozenset(permissions))))
    with pytest.raises(AuthError) as caught: save(v)
    assert caught.value.code == 'FORBIDDEN'
    v.session.scalar.assert_not_awaited()


@pytest.mark.parametrize('change,code', [('version','VERSION_CONFLICT'), ('epoch','MEMBERSHIP_CHANGED'), ('member','CAST_MEMBER_UNAVAILABLE'), ('script','NOT_FOUND'), ('name','NOT_FOUND')])
def test_conflicts_do_not_write_or_invalidate(monkeypatch, change, code):
    v = setup(monkeypatch)
    if change == 'version': v.script.lock_version = 2
    elif change == 'epoch': v.member.joined_at += timedelta(seconds=1)
    else:
        original = v.session.scalar.side_effect
        async def scalar(query):
            entity = query.column_descriptions[0].get('entity')
            if entity is {'member':CrewMember, 'script':Script, 'name':SceneCharacter}[change]: return None
            return await original(query)
        v.session.scalar.side_effect = scalar
    with pytest.raises(AuthError) as caught: save(v)
    assert caught.value.code == code
    v.session.add.assert_not_called()
    module.mark_plan_review.assert_not_awaited()


def test_replay_precedes_lock_checks_but_not_current_authorization(monkeypatch):
    v = setup(monkeypatch)
    first = save(v)
    v.receipts.append(next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0], CreativeReceipt)))
    v.session.add.reset_mock(); module.mark_plan_review.reset_mock()
    assert save(v) == first
    v.session.add.assert_not_called(); module.mark_plan_review.assert_not_awaited()
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=replace(v.actor, permissions=frozenset())))
    with pytest.raises(AuthError) as caught: save(v)
    assert caught.value.code == 'FORBIDDEN'


@pytest.mark.parametrize('change,code', [('body','IDEMPOTENCY_KEY_REUSED'), ('epoch','MEMBERSHIP_CHANGED')])
def test_receipt_cannot_be_reused_for_other_payload_or_membership(monkeypatch, change, code):
    v = setup(monkeypatch); save(v)
    receipt = next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0], CreativeReceipt))
    v.receipts.append(receipt)
    if change == 'epoch': receipt.membership_joined_at = at(2)
    else: v.body = v.body.model_copy(update={'character_name':'另一个人'})
    with pytest.raises(AuthError) as caught: save(v)
    assert caught.value.code == code


def test_unbind_removes_only_binding_not_scenes_or_formal_snapshots(monkeypatch):
    v = setup(monkeypatch)
    v.bindings.append(CharacterCasting(id=uuid4(), crew_id=v.script.crew_id, script_id=v.script.id, character_name='林雨'))
    result = save(v, v.body.model_copy(update={'member_id':None, 'membership_joined_at':None}))
    assert result.member_id is None and result.lock_version == 2
    query = v.session.execute.call_args.args[0]
    assert query.table.name == 'character_castings'


def test_commit_failure_is_not_reported_as_success(monkeypatch):
    v = setup(monkeypatch)
    v.tx.__aexit__.side_effect = OperationalError('private SQL', {}, Exception('failed'))
    with pytest.raises(OperationalError): save(v)


@pytest.mark.parametrize('changes', [{'character_name':' '}, {'member_id':None}, {'membership_joined_at':None}, {'membership_joined_at':'2026-11-03T12:00:00'}, {'character_name':42}])
def test_input_requires_name_and_complete_timezone_aware_identity(changes):
    with pytest.raises(ValidationError):
        CastingSave(**({'expected_lock_version':1, 'character_name':'林雨', 'member_id':uuid4(), 'membership_joined_at':ZERO} | changes))


@pytest.mark.parametrize('state,scheduled,expected', [('unassigned',False,'CHARACTER_UNCAST'), ('invalid',True,'CAST_MEMBER_INVALID'), ('active',False,'CAST_MEMBER_UNSCHEDULED'), ('active',True,None)])
def test_casting_blocks_known_missing_assignments_and_never_auto_adds_members(state, scheduled, expected):
    item, data = context()
    member = item['members'][0]
    cast = dict(scene_revision_id=item['scene_revision_id'], character_name='林雨', display_name='陈同学', status=state,
                member_id=member['member_id'] if scheduled else uuid4(), membership_joined_at=ZERO)
    report = NS(blockers=[])
    before = list(item['members'])
    plan_casting.extend_casting(report, [item], [cast])
    assert codes(report) == ({expected} if expected else set())
    assert item['members'] == before and item['characters'] == [cast]


def test_casting_lookup_is_script_scoped_and_detects_role_or_epoch_changes(monkeypatch):
    v = setup(monkeypatch)
    scene = SceneRevision(id=uuid4(), script_version_id=v.script.current_version_id)
    character = SceneCharacter(scene_revision_id=scene.id, name='林雨')
    binding = CharacterCasting(script_id=v.script.id, character_name='林雨', member_id=v.member.id,
                               membership_joined_at=ZERO, display_name='旧称', updated_at=ZERO)
    actor_role = [v.member.id]
    async def scalars(query):
        entity = query.column_descriptions[0].get('entity')
        values = {SceneCharacter:[character], CharacterCasting:[binding], CrewMember:[v.member], MemberRole:actor_role, User:[v.member.user_id]}
        return Mock(all=Mock(return_value=values[entity]))
    v.session.scalars.side_effect = scalars
    def collect():
        return asyncio.run(plan_casting.collect_casting(v.session, v.script.crew_id, {scene.id:scene},
            {v.script.current_version_id:ScriptVersion(script_id=v.script.id)}))
    first = collect(); assert first[0]['status'] == 'active' and first[0]['display_name'] == '陈同学'
    v.member.joined_at = at(1)
    assert collect()[0]['status'] == 'invalid'
    v.member.joined_at = ZERO; actor_role.clear()
    assert collect()[0]['status'] == 'invalid'
    actor_role.append(v.member.id); binding.script_id = uuid4()
    assert collect()[0]['status'] == 'unassigned'
    binding.script_id = v.script.id; binding.updated_at = at(1)
    assert snapshot_digest({'castings':first}) != snapshot_digest({'castings':collect()})


def test_review_invalidation_filters_character_and_script_and_preserves_published_contents(monkeypatch):
    v = setup(monkeypatch)
    version = PlanVersion(id=uuid4(), review_reasons=[], publication_snapshot={'items':[]}, lock_version=3)
    v.session.scalars.return_value = Mock(all=Mock(return_value=[version]))
    from silencio_set.call_sheets import dependencies as sheets
    monkeypatch.setattr(sheets, 'mark_source_review', AsyncMock())
    asyncio.run(dependencies.mark_plan_review(v.session, v.script.crew_id, 'casting_changed', now=ZERO, script_id=v.script.id, character_name='林雨'))
    sql = str(v.session.scalars.call_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds':True}))
    assert 'scene_characters' in sql and '林雨' in sql and str(v.script.id) in sql
    assert version.review_reasons == ['casting_changed'] and version.publication_snapshot == {'items':[]} and version.lock_version == 3
    sheets.mark_source_review.assert_awaited_once_with(v.session, v.script.crew_id, version.id, ZERO)


def test_actor_selection_and_migration_keep_same_crew_and_membership_epoch(monkeypatch):
    query = str(module.actor_query(uuid4()).compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds':True}))
    assert "member_roles.role_code = 'actor'" in query and "users.status = 'active'" in query and "crew_members.status = 'active'" in query
    monkeypatch.setattr(config_module, 'load_settings', lambda: Settings(_env_file=None, database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    output = StringIO(); command.upgrade(Config('alembic.ini', output_buffer=output), 'head', sql=True)
    sql = output.getvalue()
    assert '0021_casting' in sql and 'UNIQUE (script_id, character_name)' in sql
    assert 'membership_joined_at TIMESTAMP WITH TIME ZONE NOT NULL' in sql
    assert 'CONSTRAINT fk_casting_member_id' in sql and 'CONSTRAINT fk_casting_updated_by' in sql
    assert 'FOREIGN KEY(crew_id, member_id) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT' in sql


def test_http_casting_get_save_require_csrf_and_pass_exact_user_and_request(crew_client, monkeypatch):
    client, audits, user, crew = crew_client
    repo = CreativeRepository(Mock())
    read = AsyncMock(return_value=CastingPage(script_id=uuid4(), lock_version=1, items=[], total=0, page=1, page_size=20))
    monkeypatch.setattr(module, 'read', read)
    client.app.state.creative = CreativeService(repo, audits)
    script_id = read.return_value.script_id
    url = f'/api/v1/crews/{crew.id}/scripts/{script_id}/casting'
    assert client.get(url).status_code == 200
    assert read.call_args.args[1] == user.id
    body = dict(expected_lock_version=1, character_name='林雨', member_id=None, membership_joined_at=None)
    result = NS(script_id=script_id, lock_version=2, character_name='林雨', member_id=None, membership_joined_at=None, display_name=None)
    save_mock = AsyncMock(return_value=result.__dict__); monkeypatch.setattr(module, 'save', save_mock)
    headers = {'Idempotency-Key':str(uuid4())}
    assert client.patch(url, json=body, headers=headers).status_code == 200
    assert save_mock.call_args.args[1] == user.id and save_mock.call_args.kwargs['idempotency_key'] == headers['Idempotency-Key']
    client.headers.pop('X-CSRF-Token', None)
    assert client.patch(url, json=body, headers=headers).status_code == 403
    assert save_mock.await_count == 1


def test_read_keeps_unassigned_and_stale_binding_visible_with_current_script_lock(monkeypatch):
    v = setup(monkeypatch)
    binding = CharacterCasting(script_id=v.script.id, character_name='林雨', member_id=v.member.id,
                               membership_joined_at=at(2), display_name='原演员名')
    v.session.scalar.side_effect = lambda q: v.script if q.column_descriptions[0].get('entity') is Script else 2
    v.session.scalars.side_effect = [Mock(all=Mock(return_value=rows)) for rows in
                                    (['林雨', '路人'], [binding], [v.member], ['林雨', '路人'])]
    result = asyncio.run(module.read(v.repo, v.user, v.script.crew_id, object_id=v.script.id))
    assert result.lock_version == 1 and result.total == 2
    assert [entry.status for entry in result.items] == ['invalid', 'unassigned']
    assert result.items[0].display_name == '原演员名'


def test_production_script_picker_does_not_return_script_contents(monkeypatch):
    v = setup(monkeypatch)
    v.session.scalar.return_value = 1; v.session.scalar.side_effect = None
    v.session.scalars.return_value = Mock(all=Mock(return_value=[v.script]))
    result = asyncio.run(module.read(v.repo, v.user, v.script.crew_id))
    assert result.items[0].model_dump() == {'id':v.script.id, 'title':v.script.title}


def test_actor_role_change_marks_bound_character_plans_for_review(monkeypatch):
    v = setup(monkeypatch)
    v.session.scalars.return_value = Mock(all=Mock(return_value=[CharacterCasting(script_id=v.script.id, character_name='林雨')]))
    asyncio.run(module.mark_member_casting_review(v.session, v.script.crew_id, v.member.id, ZERO))
    module.mark_plan_review.assert_awaited_once_with(v.session, v.script.crew_id, 'casting_role_changed', now=ZERO,
                                                   script_id=v.script.id, character_name='林雨')


def test_organization_role_write_invokes_casting_review_in_same_transaction(monkeypatch):
    v = setup(monkeypatch)
    from silencio_set.crews import organization
    from silencio_set.crews.schemas import MemberRolesUpdate
    from test_member_roles import member
    target = member()
    monkeypatch.setattr(organization, 'organization_actor', AsyncMock())
    monkeypatch.setattr(organization, 'role_map', AsyncMock(return_value={target.id:['actor']}))
    mark = AsyncMock(); monkeypatch.setattr(module, 'mark_member_casting_review', mark)
    v.session.scalar.side_effect = [target, ZERO]
    result = asyncio.run(organization.OrganizationRepository(v.repo.sessions).replace_roles(v.user, target.crew_id, target.id,
        MemberRolesUpdate(role_codes=['staff'], expected_lock_version=1), 'a'*32))
    assert result.role_codes == ['staff'] and result.lock_version == 2
    mark.assert_awaited_once_with(v.session, target.crew_id, target.id, ZERO)


def test_real_preflight_composition_includes_casting_blockers_and_frozen_evidence(monkeypatch):
    from silencio_set.plans import checking
    from silencio_set.creative.models import Breakdown
    from silencio_set.resources.models import Resource, Reservation
    v = setup(monkeypatch)
    item, data = context()
    source = SceneRevision(id=item['scene_revision_id'], script_version_id=v.script.current_version_id, breakdown_id=uuid4())
    script_version = ScriptVersion(id=v.script.current_version_id, script_id=v.script.id)
    resource = next(iter(data['resources'].values()))
    resource.id = item['location_resource_id']; resource.unit = '处'; resource.stock_quantity = 0; resource.lock_version = 1
    booking = next(iter(data['reservations'].values())); booking.lock_version = 1
    person_id = item['members'][0]['member_id']
    person = CrewMember(id=person_id, user_id=uuid4(), status='active', joined_at=ZERO)
    mappings = {SceneRevision:{source.id:source}, Breakdown:{source.breakdown_id:NS(id=source.breakdown_id, script_version_id=source.script_version_id, lock_version=1, review_status='confirmed')},
        ScriptVersion:{script_version.id:script_version}, Script:{v.script.id:v.script}, CrewMember:{person_id:person},
        Resource:{resource.id:resource}, Reservation:{booking.id:booking}}
    monkeypatch.setattr(checking, 'session_items', AsyncMock(return_value=[item]))
    monkeypatch.setattr(checking, 'rows_by_ids', AsyncMock(side_effect=lambda session, model, crew, ids: mappings[model]))
    monkeypatch.setattr(checking, 'current_other_items', AsyncMock(return_value=[]))
    monkeypatch.setattr(checking, 'scoped', AsyncMock(return_value=NS(current_published_version_id=None)))
    cast = dict(scene_revision_id=source.id, character_name='林雨', member_id=None, membership_joined_at=None, display_name=None, status='unassigned')
    monkeypatch.setattr(checking, 'collect_casting', AsyncMock(return_value=[cast]))
    version = NS(id=uuid4(), crew_id=v.script.crew_id, plan_id=uuid4(), status='draft', lock_version=1)
    report, snapshot = asyncio.run(checking.collect_check(v.session, version, ZERO))
    assert 'CHARACTER_UNCAST' in codes(report)
    assert snapshot['castings'] == [cast] and snapshot['items'][0]['characters'] == [cast]
