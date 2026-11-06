import asyncio
from dataclasses import replace
from io import StringIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.dialects import postgresql

from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.creative import source_reviews as module
from silencio_set.creative.source_evidence import current_reviews, assessment_status
from silencio_set.creative.source_schemas import SourceReviewInput
from silencio_set.creative.models import Breakdown, StoryboardVersion, SceneRevision, SourceReviewRecord, CreativeReceipt
from silencio_set.crews.models import AuditLog
from silencio_set.plans.domain import check_plan
from silencio_set.plans.publication import snapshot_digest
from silencio_set.scripts.models import Script, ScriptVersion
from silencio_set.scripts.schemas import WorkingVersion
from test_creative import fixture as creative_fixture
from test_stock_scripts import script_fixture, script_write
from test_plans import context as plan_context, codes, ZERO, at
from test_auth_api import auth_client
from test_crews import crew_client


def fixture(monkeypatch, kind='breakdowns'):
    repo, session, tx, actor, target, fixed, scene = creative_fixture(monkeypatch,kind)
    target.review_status = 'confirmed'
    fixed.version_no = 1
    working = ScriptVersion(id=uuid4(), crew_id=target.crew_id, script_id=fixed.script_id, version_no=2, status='confirmed')
    script = Script(id=fixed.script_id, crew_id=target.crew_id, title='雨停之前', current_version_id=working.id, working_revision=2, lock_version=5)
    rows, receipts = [], []
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=actor))
    monkeypatch.setattr(module,'mark_plan_review',AsyncMock())
    async def scalar(q):
        if 'count(' in str(q): return len(rows)
        entity = q.column_descriptions[0].get('entity')
        if entity is CreativeReceipt: return receipts[0] if receipts else None
        if entity is ScriptVersion:
            return working if q.compile().params.get('id_1') == working.id else fixed
        return {Script:script, Breakdown:target, StoryboardVersion:target, SceneRevision:scene}.get(entity, at(1))
    session.scalar.side_effect = scalar
    session.scalars.side_effect = lambda q: Mock(all=Mock(return_value=rows))
    session.execute.return_value = Mock(all=Mock(return_value=[(target,fixed)]))
    body = SourceReviewInput(expected_lock_version=target.lock_version, expected_working_version_id=working.id,
        expected_working_revision=2, expected_review_id=None, conclusion='still_applicable', review_note=' 已核对第二版第1—3段，台词变化不影响该份场戏安排。 ')
    return NS(repo=repo,session=session,tx=tx,actor=actor,target=target,fixed=fixed,working=working,script=script,
              rows=rows,receipts=receipts,kind=kind,body=body,user=uuid4())


def review(v, body=None):
    return asyncio.run(module.review(v.repo,v.user,v.script.crew_id,v.kind,body or v.body,'a'*32,
        object_id=v.target.id,idempotency_key='original-key'))


def saved(v):
    return next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0],SourceReviewRecord))


@pytest.mark.parametrize('kind',['breakdowns','storyboards'])
def test_review_freezes_basis_without_rewriting_target_and_preserves_audit_receipt(monkeypatch,kind):
    v=fixture(monkeypatch,kind); before=v.target.lock_version
    result=review(v); record=saved(v)
    assert result.status=='still_applicable' and result.latest_review.id==record.id
    assert record.working_revision==2 and record.target_lock_version==before
    assert record.source_script_version_id==v.fixed.id and record.working_script_version_id==v.working.id
    assert v.target.lock_version==before and v.target.review_status=='confirmed'
    types={type(call.args[0]) for call in v.session.add.call_args_list}
    assert types=={SourceReviewRecord,CreativeReceipt,AuditLog}
    v.tx.__aexit__.assert_awaited_once_with(None,None,None)
    if kind=='breakdowns': module.mark_plan_review.assert_awaited_once_with(v.session,v.script.crew_id,'source_review_changed',now=at(1),breakdown_id=v.target.id)
    else: module.mark_plan_review.assert_not_awaited()


@pytest.mark.parametrize('change,code', [('permission','FORBIDDEN'),('lock','VERSION_CONFLICT'),('draft','SOURCE_TARGET_NOT_CONFIRMED'),
    ('working','SOURCE_BASIS_CHANGED'),('epoch','SOURCE_BASIS_CHANGED'),('same','SOURCE_REVIEW_NOT_REQUIRED'),('previous','SOURCE_REVIEW_CHANGED')])
def test_stale_or_unauthorized_review_does_not_partially_write(monkeypatch,change,code):
    v=fixture(monkeypatch)
    if change=='permission': monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(v.actor,permissions=v.actor.permissions-{'scene.review'})))
    elif change=='lock': v.target.lock_version+=1
    elif change=='draft': v.target.review_status='draft'
    elif change=='working': v.script.current_version_id=uuid4()
    elif change=='epoch': v.script.working_revision+=2
    elif change=='same': v.fixed.id=v.script.current_version_id
    else: v.body=v.body.model_copy(update={'expected_review_id':uuid4()})
    with pytest.raises(AuthError) as caught: review(v)
    assert caught.value.code==code
    v.session.add.assert_not_called(); module.mark_plan_review.assert_not_awaited()


def test_committed_replay_returns_original_basis_without_new_review_after_working_switch(monkeypatch):
    v=fixture(monkeypatch); first=review(v)
    v.receipts.append(next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0],CreativeReceipt)))
    v.script.working_revision=4; v.session.add.reset_mock(); module.mark_plan_review.reset_mock()
    assert review(v)==first and first.working_revision==2
    v.session.add.assert_not_called(); module.mark_plan_review.assert_not_awaited()
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(v.actor,permissions=frozenset())))
    with pytest.raises(AuthError) as caught: review(v)
    assert caught.value.code=='FORBIDDEN'


@pytest.mark.parametrize('change,code',[('payload','IDEMPOTENCY_KEY_REUSED'),('membership','MEMBERSHIP_CHANGED')])
def test_receipt_cannot_cross_payload_or_membership(monkeypatch,change,code):
    v=fixture(monkeypatch); review(v)
    receipt=next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0],CreativeReceipt)); v.receipts.append(receipt)
    if change=='payload': v.body=v.body.model_copy(update={'conclusion':'needs_revision'})
    else: receipt.membership_joined_at=at(3)
    with pytest.raises(AuthError) as caught: review(v)
    assert caught.value.code==code


def test_commit_failure_does_not_report_a_review_as_saved(monkeypatch):
    v=fixture(monkeypatch); v.tx.__aexit__.side_effect=OperationalError('private SQL',{},Exception('failed'))
    with pytest.raises(OperationalError): review(v)


def test_latest_decision_is_bound_to_working_epoch_even_when_returning_to_same_version(monkeypatch):
    v=fixture(monkeypatch); review(v); original=saved(v); v.rows.append(original)
    def current(): return asyncio.run(current_reviews(v.session,v.script.crew_id,v.kind,{v.target.id:(v.target,v.fixed.id,v.script)}))
    assert current()[v.target.id].conclusion=='still_applicable'
    v.script.lock_version+=1  # Importing another version does not change the working epoch.
    assert current()[v.target.id].id==original.id
    v.script.working_revision=4  # B -> A -> B: same version ID, new epoch.
    assert current()=={}
    v.script.working_revision=2
    newer=SourceReviewRecord(id=uuid4(),breakdown_id=v.target.id,target_lock_version=v.target.lock_version,
        source_script_version_id=v.fixed.id,working_script_version_id=v.working.id,working_revision=2,conclusion='needs_revision')
    v.rows.insert(0,newer)
    assert current()[v.target.id].conclusion=='needs_revision'
    v.target.lock_version+=1
    assert current()=={}


def test_current_source_and_unconfirmed_drafts_do_not_inherit_historical_reviews(monkeypatch):
    v=fixture(monkeypatch)
    v.target.review_status='draft'
    assert asyncio.run(current_reviews(v.session,v.script.crew_id,v.kind,{v.target.id:(v.target,v.fixed.id,v.script)}))=={}
    v.session.scalars.assert_not_awaited()
    assert assessment_status(v.fixed.id,v.script,None)=='review_required'
    v.script.current_version_id=v.fixed.id
    assert assessment_status(v.fixed.id,v.script,None)=='current'
    v.script.current_version_id=None
    assert assessment_status(v.fixed.id,v.script,None)=='no_working_version'


def test_source_applicability_never_waives_known_calendar_conflicts():
    item,data=plan_context(); source=data['sources'][item['scene_revision_id']]
    source.update(current_version_id='different-work',source_review_applicable=True,source_review_id=uuid4())
    assert 'SOURCE_REVIEW_REQUIRED' not in codes(check_plan([item],**data))
    data['calendars'][item['members'][0]['member_id']][0].availability='unavailable'
    assert 'MEMBER_UNAVAILABLE' in codes(check_plan([item],**data))
    before=snapshot_digest(source); source['source_review_id']=uuid4()
    assert snapshot_digest(source)!=before


@pytest.mark.parametrize('kind',['breakdowns','storyboards'])
def test_impact_listing_and_history_keep_fixed_sources_and_current_conclusions(monkeypatch,kind):
    v=fixture(monkeypatch,kind); review(v); v.rows.append(saved(v))
    assessment=asyncio.run(module.read(v.repo,v.user,v.script.crew_id,kind,object_id=v.target.id))
    assert assessment.status=='still_applicable' and assessment.working_script_version_no==2
    listing=asyncio.run(module.read(v.repo,v.user,v.script.crew_id,kind,script_id=v.script.id))
    assert listing.items[0].source_script_version_id==v.fixed.id and listing.total==1
    sql=str(v.session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert 'IS DISTINCT FROM' in sql and 'script_versions.script_id' in sql
    history=asyncio.run(module.read(v.repo,v.user,v.script.crew_id,kind,object_id=v.target.id,history=True))
    assert history.items[0].id==v.rows[0].id and history.total==1


def test_reselecting_same_working_version_does_not_invalidate_source_reviews(monkeypatch):
    repo,session,_,actor,script,version=script_fixture(monkeypatch)
    from silencio_set.plans import dependencies
    mark=AsyncMock(); monkeypatch.setattr(dependencies,'mark_plan_review',mark)
    version.status='confirmed'; script.current_version_id=version.id; script.working_revision=3
    script_write(repo,actor,script,'set_working',WorkingVersion(expected_lock_version=1,version_id=version.id))
    assert script.working_revision==3 and script.lock_version==2
    mark.assert_not_awaited()
    script.current_version_id=uuid4()
    script_write(repo,actor,script,'set_working',WorkingVersion(expected_lock_version=2,version_id=version.id))
    assert script.working_revision==4
    mark.assert_awaited_once()


@pytest.mark.parametrize('changes',[{'review_note':' '},{'review_note':42},{'review_note':'a'*1001},{'expected_working_revision':True},{'conclusion':'force'}])
def test_review_input_requires_traceable_note_and_explicit_current_basis(changes):
    body=dict(expected_lock_version=1,expected_working_version_id=uuid4(),expected_working_revision=2,
              expected_review_id=None,conclusion='still_applicable',review_note='已核对工作稿第2段')
    with pytest.raises(ValidationError): SourceReviewInput(**(body|changes))


def test_migration_backfills_working_epochs_and_preserves_same_crew_source_references(monkeypatch):
    monkeypatch.setattr(config_module,'load_settings',lambda:Settings(_env_file=None,database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    out=StringIO(); command.upgrade(Config('alembic.ini',output_buffer=out),'head',sql=True)
    sql=out.getvalue()
    assert '0023_source_reviews' in sql and 'UPDATE scripts SET working_revision = 1 WHERE current_version_id IS NOT NULL' in sql
    assert 'FOREIGN KEY(crew_id, script_id, working_script_version_id) REFERENCES script_versions (crew_id, script_id, id)' in sql
    assert 'ix_source_reviews_breakdown' in sql and 'ix_source_reviews_storyboard' in sql


def test_http_review_requires_csrf_key_and_keeps_history_reads_independent(crew_client,monkeypatch):
    client,_,_,crew=crew_client; v=fixture(monkeypatch)
    result=module.assessment(v.kind,v.target,v.fixed,v.script,v.working,None)
    service=Mock(execute=AsyncMock(return_value=result)); client.app.state.creative=service
    path=f'/api/v1/crews/{crew.id}/breakdowns/{v.target.id}'
    assert client.get(path+'/source-assessment').status_code==200
    assert client.post(path+'/source-reviews',json=v.body.model_dump(mode='json')).status_code==422
    assert client.post(path+'/source-reviews',json=v.body.model_dump(mode='json'),headers={'Idempotency-Key':str(uuid4())}).status_code==201
    assert service.execute.call_args.args[1]=='source_review'
    client.headers.pop('X-CSRF-Token',None)
    assert client.post(path+'/source-reviews',json=v.body.model_dump(mode='json'),headers={'Idempotency-Key':str(uuid4())}).status_code==403


def test_preflight_reads_real_source_review_and_fingerprints_changed_conclusion(monkeypatch):
    from silencio_set.plans import checking
    from silencio_set.accounts.models import User
    from silencio_set.crews.models import CrewMember
    from silencio_set.resources.models import Resource, Reservation
    v=fixture(monkeypatch); review(v); v.rows.append(saved(v))
    item,data=plan_context(); item['scene_revision_id']=v.target.id  # Replace with the actual fixed scene below.
    scene=SceneRevision(id=uuid4(),script_version_id=v.fixed.id,breakdown_id=v.target.id)
    item['scene_revision_id']=scene.id
    person=CrewMember(id=item['members'][0]['member_id'],user_id=uuid4(),status='active',joined_at=ZERO)
    resource=next(iter(data['resources'].values())); resource.id=item['location_resource_id']; resource.unit='处'; resource.stock_quantity=0; resource.lock_version=1
    booking=next(iter(data['reservations'].values())); booking.lock_version=1
    mapping={SceneRevision:{scene.id:scene},Breakdown:{v.target.id:v.target},ScriptVersion:{v.fixed.id:v.fixed},
        Script:{v.script.id:v.script},CrewMember:{person.id:person},Resource:{resource.id:resource},Reservation:{booking.id:booking}}
    monkeypatch.setattr(checking,'session_items',AsyncMock(return_value=[item]))
    monkeypatch.setattr(checking,'rows_by_ids',AsyncMock(side_effect=lambda session,model,crew,ids:mapping[model]))
    monkeypatch.setattr(checking,'current_other_items',AsyncMock(return_value=[]))
    monkeypatch.setattr(checking,'scoped',AsyncMock(return_value=NS(current_published_version_id=None)))
    async def scalars(q):
        entity=q.column_descriptions[0].get('entity')
        rows=v.rows if entity is SourceReviewRecord else [person.user_id] if entity is User else []
        return Mock(all=Mock(return_value=rows))
    v.session.scalars.side_effect=scalars
    version=NS(id=uuid4(),crew_id=v.script.crew_id,plan_id=uuid4(),status='draft',lock_version=1)
    report,before=asyncio.run(checking.collect_check(v.session,version,ZERO))
    assert 'SOURCE_REVIEW_REQUIRED' not in codes(report)
    assert before['sources'][0]['source_review_id']==v.rows[0].id
    v.rows.insert(0,SourceReviewRecord(id=uuid4(),breakdown_id=v.target.id,source_script_version_id=v.fixed.id,
        working_script_version_id=v.working.id,working_revision=2,target_lock_version=v.target.lock_version,conclusion='needs_revision'))
    report,after=asyncio.run(checking.collect_check(v.session,version,ZERO))
    assert 'SOURCE_REVIEW_REQUIRED' in codes(report) and snapshot_digest(before)!=snapshot_digest(after)


def test_source_review_invalidation_is_scoped_to_referencing_breakdown(monkeypatch):
    from silencio_set.plans import dependencies
    from silencio_set.plans.models import PlanVersion
    from silencio_set.call_sheets import dependencies as sheets
    v=fixture(monkeypatch)
    plan=PlanVersion(id=uuid4(),lock_version=3,review_reasons=[],publication_snapshot={'items':[]})
    v.session.scalars.side_effect=None; v.session.scalars.return_value=Mock(all=Mock(return_value=[plan]))
    mark=AsyncMock(); monkeypatch.setattr(sheets,'mark_source_review',mark)
    asyncio.run(dependencies.mark_plan_review(v.session,v.script.crew_id,'source_review_changed',now=ZERO,breakdown_id=v.target.id))
    sql=str(v.session.scalars.call_args.args[0].compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    assert str(v.target.id) in sql and 'scene_revisions.breakdown_id =' in sql
    assert plan.review_reasons==['source_review_changed'] and plan.publication_snapshot=={'items':[]}
    mark.assert_awaited_once_with(v.session,v.script.crew_id,plan.id,ZERO)
