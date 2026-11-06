import asyncio
from datetime import timedelta
from io import StringIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy.exc import OperationalError
from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.creative import repository as module
from silencio_set.creative.models import Breakdown, Scene, SceneRevision, StoryboardVersion, CreativeReview, CreativeReceipt, SceneCharacter, SceneResourceNeed
from silencio_set.creative.schemas import Source, SceneInput, SceneSave, ShotInput, ShotSave, Decision
from silencio_set.resources.repository import ResourceActor
from silencio_set.scripts.models import ScriptVersion, ScriptParagraph
from silencio_set.tasks.schemas import VersionInput
from silencio_set.tasks.repository import request_digest
from test_account_repository import fake_session_factory
from test_resources import EPOCH, hour
from test_auth_api import auth_client
from test_crews import crew_client


def fixture(monkeypatch,kind='breakdowns'):
    factory,session,tx=fake_session_factory(); crew=uuid4(); member=uuid4()
    actor=ResourceActor(member,frozenset({'script.read','scene.read','scene.edit','scene.review','storyboard.read','storyboard.edit','storyboard.review'}),frozenset({'director'}),EPOCH)
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=actor))
    source=ScriptVersion(id=uuid4(),crew_id=crew,script_id=uuid4(),status='confirmed')
    parent=Breakdown(id=uuid4(),crew_id=crew,script_version_id=source.id,version_no=1,review_status='draft' if kind=='breakdowns' else 'confirmed',lock_version=1,reviewed_by=None,reviewed_at=None,created_at=hour(0),updated_at=hour(0))
    scene=Scene(id=uuid4(),crew_id=crew,script_id=source.script_id)
    entry=SceneRevision(id=uuid4(),crew_id=crew,scene_id=scene.id,breakdown_id=parent.id,script_version_id=source.id,scene_number='01',source_start=1,source_end=2,content={'location_text':'走廊','time_of_day':'day','production_note':None})
    board=StoryboardVersion(id=uuid4(),crew_id=crew,scene_revision_id=entry.id,version_no=1,review_status='draft',lock_version=1,reviewed_by=None,reviewed_at=None,shots=[{'shot_number':'01','shot_size':'全景','visual_content':'走廊空镜','sort_order':1}],created_at=hour(0),updated_at=hour(0))
    async def scalar(q):
        if 'max(' in str(q): return 0
        entity=q.column_descriptions[0].get('entity')
        return {CreativeReceipt:None,ScriptVersion:source,Breakdown:parent,Scene:scene,SceneRevision:entry,StoryboardVersion:board}.get(entity,hour(0))
    async def scalars(q):
        entity=q.column_descriptions[0].get('entity')
        return Mock(all=Mock(return_value=[] if entity in {SceneCharacter,SceneResourceNeed} else [1,2,3] if entity is ScriptParagraph else [entry]))
    session.scalar.side_effect=scalar; session.scalars.side_effect=scalars
    return module.CreativeRepository(factory),session,tx,actor,parent if kind=='breakdowns' else board,source,entry


def run(repo,row,action,body,kind='breakdowns'):
    return asyncio.run(repo.mutate(uuid4(),row.crew_id,kind,action,body,'a'*32,idempotency_key=str(uuid4()),object_id=None if action=='create' else row.id))


@pytest.mark.parametrize('change',[{'source_end':0},{'source_start':3,'source_end':1},{'scene_number':' '},{'source_end':100001}])
def test_scene_source_and_names_are_bounded(change):
    with pytest.raises(ValidationError): SceneInput(**({'scene_number':'01','location_text':'走廊','source_start':1,'source_end':2}|change))


def test_repeated_scene_and_shot_numbers_are_rejected():
    item=SceneInput(scene_number='01',location_text='走廊',source_start=1,source_end=2)
    with pytest.raises(ValidationError): SceneSave(expected_lock_version=1,scenes=[item,item])
    shot=ShotInput(shot_number='1',shot_size='全景',visual_content='空镜')
    with pytest.raises(ValidationError): ShotSave(expected_lock_version=1,shots=[shot,shot])
    with pytest.raises(ValidationError): ShotInput(shot_number='1',shot_size='全景',visual_content='空镜',duration_seconds=0)
    with pytest.raises(ValidationError): Decision(expected_lock_version=1,decision='reject')


@pytest.mark.parametrize('kind',['breakdowns','storyboards'])
def test_create_keeps_a_draft_with_a_verified_source(monkeypatch,kind):
    repo,session,tx,_,row,source,entry=fixture(monkeypatch,kind)
    result=run(repo,row,'create',Source(source_id=source.id if kind=='breakdowns' else entry.id),kind)
    assert result.review_status=='draft' and result.version_no==1
    tx.__aexit__.assert_awaited_once_with(None,None,None)
    assert any(isinstance(c.args[0],CreativeReceipt) for c in session.add.call_args_list)


def test_unconfirmed_script_cannot_be_broken_down(monkeypatch):
    repo,session,_,_,row,source,_=fixture(monkeypatch); source.status='pending_check'
    with pytest.raises(AuthError) as caught: run(repo,row,'create',Source(source_id=source.id))
    assert caught.value.code=='SOURCE_NOT_CONFIRMED'
    session.add.assert_not_called()


def test_save_validates_real_source_paragraphs_and_preserves_explicit_scene_identity(monkeypatch):
    repo,session,_,_,row,_,entry=fixture(monkeypatch)
    result=run(repo,row,'save',SceneSave(expected_lock_version=1,scenes=[SceneInput(scene_id=entry.scene_id,scene_number='03A',location_text='医院走廊',source_start=1,source_end=3)]))
    assert result.lock_version==2
    revision=next(c.args[0] for c in session.add.call_args_list if isinstance(c.args[0],SceneRevision))
    assert revision.scene_id==entry.scene_id and revision.scene_number=='03A' and revision.content['location_text']=='医院走廊'


def test_out_of_source_range_aborts_save(monkeypatch):
    repo,session,_,_,row,_,_=fixture(monkeypatch)
    with pytest.raises(AuthError) as caught: run(repo,row,'save',SceneSave(expected_lock_version=1,scenes=[SceneInput(scene_number='1',location_text='走廊',source_start=1,source_end=4)]))
    assert caught.value.code=='INVALID_SOURCE_RANGE'
    session.add.assert_not_called()


def test_submit_review_reject_reopen_keep_review_snapshot(monkeypatch):
    repo,session,_,_,row,_,_=fixture(monkeypatch)
    assert run(repo,row,'submit',VersionInput(expected_lock_version=1)).review_status=='pending_review'
    assert run(repo,row,'review',Decision(expected_lock_version=2,decision='reject',review_note='补勘景说明')).review_status=='rejected'
    review=next(c.args[0] for c in session.add.call_args_list if isinstance(c.args[0],CreativeReview))
    assert review.content_snapshot['items'][0]['scene_number']=='01' and review.reviewed_lock_version==2
    assert run(repo,row,'reopen',VersionInput(expected_lock_version=3)).review_status=='draft'
    assert review.review_note=='补勘景说明' and row.reviewed_by is None


@pytest.mark.parametrize('state',['pending_review','confirmed','rejected'])
def test_only_draft_content_can_be_edited(monkeypatch,state):
    repo,session,_,_,row,_,_=fixture(monkeypatch); row.review_status=state
    with pytest.raises(AuthError) as caught: run(repo,row,'save',SceneSave(expected_lock_version=1,scenes=[]))
    assert caught.value.code=='CREATIVE_IMMUTABLE'
    session.execute.assert_not_awaited()


def test_confirmed_versions_cannot_be_reopened(monkeypatch):
    repo,_,_,_,row,_,_=fixture(monkeypatch); row.review_status='confirmed'
    with pytest.raises(AuthError): run(repo,row,'reopen',VersionInput(expected_lock_version=1))


def test_storyboard_saves_order_and_cannot_create_from_unconfirmed_scene(monkeypatch):
    repo,session,_,_,row,_,entry=fixture(monkeypatch,'storyboards')
    result=run(repo,row,'save',ShotSave(expected_lock_version=1,shots=[ShotInput(shot_number='10',shot_size='近景',visual_content='主角抬头'),ShotInput(shot_number='2',shot_size='全景',visual_content='走廊')]),'storyboards')
    assert [item['sort_order'] for item in result.items]==[1,2] and result.items[0]['shot_number']=='10'
    original=session.scalar.side_effect
    async def scalar(q):
        value=await original(q)
        if q.column_descriptions[0].get('entity') is Breakdown: value.review_status='draft'
        return value
    session.scalar.side_effect=scalar
    with pytest.raises(AuthError) as caught: run(repo,row,'create',Source(source_id=entry.id),'storyboards')
    assert caught.value.code=='SOURCE_NOT_CONFIRMED'


def test_stale_version_and_missing_review_permission_never_decide(monkeypatch):
    repo,session,_,actor,row,_,_=fixture(monkeypatch); row.review_status='pending_review'
    module.resource_actor.return_value=ResourceActor(actor.member_id,actor.permissions-{'scene.review'},actor.roles,EPOCH)
    with pytest.raises(AuthError) as caught: run(repo,row,'review',Decision(expected_lock_version=1,decision='accept'))
    assert caught.value.code=='FORBIDDEN'
    module.resource_actor.return_value=actor
    with pytest.raises(AuthError) as caught: run(repo,row,'review',Decision(expected_lock_version=2,decision='accept'))
    assert caught.value.code=='VERSION_CONFLICT'
    session.add.assert_not_called()


def test_empty_breakdown_cannot_be_submitted(monkeypatch):
    repo,session,_,_,row,_,_=fixture(monkeypatch)
    session.scalars.side_effect=None; session.scalars.return_value=Mock(all=Mock(return_value=[]))
    with pytest.raises(AuthError) as caught: run(repo,row,'submit',VersionInput(expected_lock_version=1))
    assert caught.value.code=='EMPTY_CREATIVE'


def test_previous_membership_receipt_is_not_replayed(monkeypatch):
    repo,session,_,_,row,_,_=fixture(monkeypatch)
    old=session.scalar.side_effect
    async def scalar(q):
        if q.column_descriptions[0].get('entity') is CreativeReceipt: return CreativeReceipt(membership_joined_at=EPOCH-timedelta(days=1))
        return await old(q)
    session.scalar.side_effect=scalar
    with pytest.raises(AuthError) as caught: run(repo,row,'submit',VersionInput(expected_lock_version=1))
    assert caught.value.code=='MEMBERSHIP_CHANGED'


def test_commit_failure_does_not_report_approval_success(monkeypatch):
    repo,_,tx,_,row,_,_=fixture(monkeypatch); row.review_status='pending_review'
    tx.__aexit__.side_effect=OperationalError('private SQL',{},Exception('lost commit'))
    with pytest.raises(OperationalError): run(repo,row,'review',Decision(expected_lock_version=1,decision='accept'))


def test_new_routes_do_not_match_other_membership_paths(crew_client):
    client,_,_,crew=crew_client
    assert client.put(f'/api/v1/crews/{crew.id}/members/{uuid4()}/availability',json={}).status_code==404
    client.app.state.creative=Mock()
    assert client.post(f'/api/v1/crews/{crew.id}/breakdowns',json={'source_id':str(uuid4())}).status_code==422
    assert client.post(f'/api/v1/crews/{crew.id}/breakdowns',json={'source_id':str(uuid4())},headers={'Idempotency-Key':str(uuid4()),'X-CSRF-Token':'bad'}).status_code==403


def test_creative_migration_has_source_and_number_constraints(monkeypatch):
    monkeypatch.setattr(config_module,'load_settings',lambda:Settings(_env_file=None,database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    out=StringIO(); command.upgrade(Config('alembic.ini',output_buffer=out),'head',sql=True)
    sql=out.getvalue()
    assert '0013_creative' in sql and 'UNIQUE (breakdown_id, scene_number)' in sql
    assert 'FOREIGN KEY(crew_id, breakdown_id, script_version_id)' in sql
