import asyncio
from datetime import timedelta
from io import StringIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy.dialects.postgresql import dialect

from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.resources import stock as stock_module
from silencio_set.resources import custody
from silencio_set.resources.stock import StockCreate, StockChange, StockRepository, ConsumableMovement, StockAuthorization
from silencio_set.resources.stock import StockResult, stock_view
from silencio_set.resources.models import ResourceReceipt
from silencio_set.resources.repository import ResourceActor
from silencio_set.scripts import repository as scripts
from silencio_set.scripts.models import Script, ScriptVersion, ScriptReceipt, ScriptParagraph
from silencio_set.scripts.schemas import ScriptCreate, ScriptText, WorkingVersion, ScriptResult, ScriptView
from silencio_set.scripts.service import ScriptService
from silencio_set.tasks.schemas import VersionInput
from test_account_repository import fake_session_factory
from test_resources import setup_repository, hour, EPOCH
from test_auth_api import auth_client
from test_crews import crew_client


@pytest.mark.parametrize('kind,delta', [('issue',1), ('restock',-1), ('return',-1), ('adjustment',0)])
def test_stock_direction_is_a_business_rule(kind,delta):
    with pytest.raises(ValidationError): StockChange(expected_lock_version=1, movement_type=kind, quantity_delta=delta, reason='依据')


def stock_fixture(monkeypatch):
    repo, session, transaction, member, actor, resource, _ = setup_repository(monkeypatch)
    stock_module.resources.resource_actor.return_value = ResourceActor(actor.member_id, actor.permissions | {'stock.manage'}, actor.roles, EPOCH)
    stock_module.resources.scope_codes.return_value = {'stock.manage'}
    resource.resource_type='consumable'; resource.total_quantity=None; resource.stock_quantity=5
    return StockRepository(repo.sessions), session, transaction, member, resource


def change(repo, member, resource, **options):
    body = StockChange(**({'expected_lock_version':1,'movement_type':'issue','quantity_delta':-2,'reason':'布景使用'} | options))
    return asyncio.run(repo.mutate(member.user_id, member.crew_id, 'stock_change', body, 'a'*32, resource_id=resource.id, idempotency_key=str(uuid4())))


def test_inventory_balance_and_immutable_movement_commit_together(monkeypatch):
    repo, session, transaction, member, resource = stock_fixture(monkeypatch)
    result=change(repo,member,resource)
    assert result.resource.stock_quantity == 3 and result.resource.lock_version == 2
    assert result.movement.quantity_delta == -2 and result.movement.balance_after == 3
    rows=[call.args[0] for call in session.add.call_args_list]
    assert any(isinstance(row,ConsumableMovement) for row in rows) and any(isinstance(row,ResourceReceipt) for row in rows)
    transaction.__aexit__.assert_awaited_once_with(None,None,None)


@pytest.mark.parametrize('kind,delta,expected', [('restock',4,9),('return',2,7),('adjustment',-3,2)])
def test_inventory_operations_preserve_signed_ledger(monkeypatch,kind,delta,expected):
    repo,_,_,member,resource=stock_fixture(monkeypatch)
    assert change(repo,member,resource,movement_type=kind,quantity_delta=delta).resource.stock_quantity == expected


@pytest.mark.parametrize('case,code', [('negative','STOCK_QUANTITY_CONFLICT'), ('scope','FORBIDDEN'), ('version','VERSION_CONFLICT'), ('disabled','RESOURCE_UNAVAILABLE')])
def test_stock_failure_never_creates_a_movement(monkeypatch,case,code):
    repo,session,_,member,resource=stock_fixture(monkeypatch)
    if case=='scope': stock_module.resources.scope_codes.return_value=set()
    if case=='version': resource.lock_version=2
    if case=='disabled': resource.status='disabled'
    with pytest.raises(AuthError) as caught: change(repo,member,resource,quantity_delta=-6 if case=='negative' else -1)
    assert caught.value.code==code
    session.add.assert_not_called()


def test_stock_detail_exposes_actions_from_effective_permission_and_scope(monkeypatch):
    repo,_,_,member,resource=stock_fixture(monkeypatch)
    view=asyncio.run(repo.get(member.user_id,member.crew_id,resource.id))
    assert set(view.allowed_actions)=={'change','authorize'}
    stock_module.resources.scope_codes.return_value=set()
    view=asyncio.run(repo.get(member.user_id,member.crew_id,resource.id))
    assert view.allowed_actions==['authorize']


def test_stock_detail_hides_an_unknown_or_other_crew_resource(monkeypatch):
    repo,session,_,member,resource=stock_fixture(monkeypatch)
    session.scalar.return_value=None; session.scalar.side_effect=None
    with pytest.raises(AuthError) as caught: asyncio.run(repo.get(member.user_id,member.crew_id,resource.id))
    assert caught.value.code=='NOT_FOUND'


@pytest.mark.parametrize('initial', [0,3])
def test_initial_zero_does_not_create_a_zero_delta_ledger(monkeypatch,initial):
    repo,session,_,member,_=stock_fixture(monkeypatch)
    result=asyncio.run(repo.mutate(member.user_id,member.crew_id,'stock_create',StockCreate(name='胶带',unit='卷',initial_quantity=initial,manager_member_id=member.id),'a'*32,idempotency_key=str(uuid4())))
    assert result.resource.stock_quantity==initial
    assert (result.movement is None)==(initial==0)
    assert len([call for call in session.add.call_args_list if isinstance(call.args[0],ConsumableMovement)])==(0 if initial==0 else 1)


def test_stock_replay_returns_original_balance_without_second_movement(monkeypatch):
    from silencio_set.tasks.repository import request_digest
    repo,session,_,member,resource=stock_fixture(monkeypatch)
    body=StockChange(expected_lock_version=1,movement_type='issue',quantity_delta=-2,reason='布景使用')
    resource.stock_quantity=3
    snapshot=StockResult(resource=stock_view(resource),movement=None).model_dump(mode='json')
    resource.stock_quantity=7; resource.lock_version=4
    receipt=ResourceReceipt(membership_joined_at=EPOCH,request_digest=request_digest('stock_change',resource.id,body),response_snapshot=snapshot)
    old=session.scalar.side_effect
    async def scalar(statement):
        if statement.column_descriptions[0].get('entity') is ResourceReceipt: return receipt
        return await old(statement)
    session.scalar.side_effect=scalar
    result=change(repo,member,resource)
    assert result.resource.stock_quantity==3 and resource.stock_quantity==7
    session.add.assert_not_called()


def test_unauthorized_inventory_actor_cannot_write_even_with_scope(monkeypatch):
    repo,session,_,member,resource=stock_fixture(monkeypatch)
    actor=stock_module.resources.resource_actor.return_value
    stock_module.resources.resource_actor.return_value=ResourceActor(actor.member_id,actor.permissions-{'stock.manage'},actor.roles,EPOCH)
    with pytest.raises(AuthError): change(repo,member,resource)
    session.add.assert_not_called()


def test_stock_manager_can_be_transferred_without_implicit_capability(monkeypatch):
    repo,session,_,member,resource=stock_fixture(monkeypatch)
    monkeypatch.setattr(custody,'permissions_for',AsyncMock(return_value={member.id:frozenset()}))
    old_manager=resource.manager_member_id=uuid4()
    result=asyncio.run(custody.CustodyRepository(repo.sessions).mutate(member.user_id,member.crew_id,'stock_authorize',StockAuthorization(
        expected_lock_version=1,member_id=member.id,expected_member_lock_version=1,permission_codes=[],transfer_management=True),'a'*32,resource_id=resource.id,idempotency_key=str(uuid4())))
    assert resource.manager_member_id==member.id and result.resource_lock_version==2 and member.lock_version==1


def script_fixture(monkeypatch):
    factory,session,transaction=fake_session_factory()
    actor=ResourceActor(uuid4(),frozenset({'script.read','script.edit','script.confirm'}),frozenset({'director'}),EPOCH)
    monkeypatch.setattr(scripts,'resource_actor',AsyncMock(return_value=actor))
    script=Script(id=uuid4(),crew_id=uuid4(),title='雨停之前',current_version_id=None,working_revision=0,created_by=actor.member_id,lock_version=1,created_at=hour(0),updated_at=hour(0))
    version=ScriptVersion(id=uuid4(),crew_id=script.crew_id,script_id=script.id,version_no=1,status='pending_check',lock_version=1,created_by=actor.member_id,confirmed_by=None,confirmed_at=None,created_at=hour(0))
    async def scalar(statement):
        if 'max(' in str(statement): return 0
        entity=statement.column_descriptions[0].get('entity')
        if entity is ScriptReceipt: return None
        if entity is Script: return script
        if entity is ScriptVersion: return version
        return hour(0)
    session.scalar.side_effect=scalar
    session.scalars.return_value=Mock(all=Mock(return_value=[]))
    return scripts.ScriptRepository(factory),session,transaction,actor,script,version


def script_write(repo,actor,script,action,body,**options):
    return asyncio.run(repo.mutate(uuid4(),script.crew_id,action,body,'a'*32,idempotency_key=str(uuid4()),script_id=script.id,**options))


def test_text_import_has_stable_numbering_without_invented_source_pages():
    rows=scripts.paragraphs(uuid4(),uuid4(),' 第一场\n\n角色：你好。\n  第二场 ')
    assert [row.paragraph_no for row in rows]==[1,2,3]
    assert all(row.source_page is None for row in rows)
    assert rows[1].text=='角色：你好。'


@pytest.mark.parametrize('text',[' ','x'*100001,'bad\0text'],ids=['empty','too-long','control'])
def test_script_text_limits(text):
    with pytest.raises(ValidationError): ScriptText(expected_lock_version=1,text=text)


def test_new_version_does_not_automatically_become_working_version(monkeypatch):
    repo,session,transaction,actor,script,_=script_fixture(monkeypatch)
    result=script_write(repo,actor,script,'add_version',ScriptText(expected_lock_version=1,text='第一场\n你好。'))
    assert result.version.status=='pending_check' and script.current_version_id is None and script.lock_version==2
    assert len(session.add_all.call_args.args[0])==2
    transaction.__aexit__.assert_awaited_once_with(None,None,None)


def test_confirmation_and_working_version_switch_are_separate(monkeypatch):
    repo,_,_,actor,script,version=script_fixture(monkeypatch)
    result=script_write(repo,actor,script,'confirm',VersionInput(expected_lock_version=1),version_id=version.id)
    assert result.version.status=='confirmed' and script.current_version_id is None
    result=script_write(repo,actor,script,'set_working',WorkingVersion(expected_lock_version=1,version_id=version.id))
    assert result.script.current_version_id==version.id and script.lock_version==2


def test_confirmed_text_is_immutable_and_unconfirmed_version_cannot_be_working(monkeypatch):
    repo,session,_,actor,script,version=script_fixture(monkeypatch)
    with pytest.raises(AuthError) as caught: script_write(repo,actor,script,'set_working',WorkingVersion(expected_lock_version=1,version_id=version.id))
    assert caught.value.code=='SCRIPT_NOT_CONFIRMED'
    version.status='confirmed'
    with pytest.raises(AuthError) as caught: script_write(repo,actor,script,'correct',ScriptText(expected_lock_version=1,text='覆盖'),version_id=version.id)
    assert caught.value.code=='SCRIPT_VERSION_IMMUTABLE'
    session.execute.assert_not_awaited()


def test_text_correction_replaces_only_pending_version_paragraphs(monkeypatch):
    repo,session,_,actor,script,version=script_fixture(monkeypatch)
    result=script_write(repo,actor,script,'correct',ScriptText(expected_lock_version=1,text='修正正文'),version_id=version.id)
    assert result.version.lock_version==2
    sql=session.execute.call_args.args[0].compile(dialect=dialect())
    assert version.id in sql.params.values() and script.crew_id in sql.params.values()


def test_scripts_remain_private_to_creative_roles(monkeypatch):
    repo,session,_,actor,script,_=script_fixture(monkeypatch)
    scripts.resource_actor.return_value=ResourceActor(actor.member_id,frozenset({'member.authorize'}),frozenset({'admin'}),EPOCH)
    with pytest.raises(AuthError): asyncio.run(repo.read(uuid4(),script.crew_id))
    session.scalars.assert_not_awaited()


def test_script_replay_rejects_previous_membership_before_returning_private_content(monkeypatch):
    repo,session,_,actor,script,_=script_fixture(monkeypatch)
    receipt=ScriptReceipt(membership_joined_at=EPOCH-timedelta(days=1),request_digest='a'*64,response_snapshot={})
    old=session.scalar.side_effect
    async def scalar(statement):
        if statement.column_descriptions[0].get('entity') is ScriptReceipt: return receipt
        return await old(statement)
    session.scalar.side_effect=scalar
    with pytest.raises(AuthError) as caught: script_write(repo,actor,script,'add_version',ScriptText(expected_lock_version=1,text='正文'))
    assert caught.value.code=='MEMBERSHIP_CHANGED'
    session.add.assert_not_called()


def test_script_commit_failure_cannot_report_success(monkeypatch):
    from sqlalchemy.exc import OperationalError
    repo,_,transaction,actor,script,_=script_fixture(monkeypatch)
    transaction.__aexit__.side_effect=OperationalError('private SQL',{},Exception('lost commit'))
    with pytest.raises(OperationalError): script_write(repo,actor,script,'create',ScriptCreate(title='新剧本'))


def test_script_endpoint_supports_more_than_16k_text_and_keeps_csrf(crew_client):
    client,audits,user,crew=crew_client
    script=Script(id=uuid4(),crew_id=crew.id,title='影片',current_version_id=None,lock_version=1,created_at=hour(0),updated_at=hour(0))
    repository=Mock(mutate=AsyncMock(return_value=ScriptResult(script=ScriptView.model_validate(script))))
    client.app.state.scripts=ScriptService(repository,audits)
    url=f'/api/v1/crews/{crew.id}/scripts/{script.id}/versions'
    headers={'Idempotency-Key':str(uuid4())}
    response=client.post(url,json={'expected_lock_version':1,'text':'正文'*10000},headers=headers)
    assert response.status_code==201 and response.headers['cache-control']=='no-store'
    assert repository.mutate.call_args.args[0]==user.id
    assert client.post(url,json={'expected_lock_version':1,'text':'正文'},headers=headers|{'X-CSRF-Token':'bad'}).status_code==403


def test_new_migrations_keep_shared_resource_identity_and_same_script_working_pointer(monkeypatch):
    monkeypatch.setattr(config_module,'load_settings',lambda:Settings(_env_file=None,database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    out=StringIO(); command.upgrade(Config('alembic.ini',output_buffer=out),'head',sql=True)
    sql=out.getvalue()
    assert '0012_scripts' in sql and 'stock_quantity INTEGER' in sql and 'CREATE TABLE consumable_movements' in sql
    assert 'fk_script_current_version' in sql and 'REFERENCES script_versions (crew_id, script_id, id)' in sql
