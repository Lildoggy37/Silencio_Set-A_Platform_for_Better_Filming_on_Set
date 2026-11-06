import asyncio
from dataclasses import replace
from io import StringIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.changes import checking as module
from silencio_set.changes import history
from silencio_set.changes.models import ImpactCheck, ImpactIssue, ImpactTransition
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.creative.models import SourceReviewRecord
from silencio_set.crews.models import AuditLog
from silencio_set.notifications.schemas import EmptyInput
from test_change_workbench import fixture as workbench_fixture
from test_plans import ZERO, at
from test_crews import crew_client
from test_auth_api import auth_client


def fixture(monkeypatch,category='breakdowns'):
    v,repo,actor,rows=workbench_fixture(monkeypatch,category)
    for row in rows: row[0].lock_version=2
    if category=='call_sheets': rows[0][1].current_published_version_id=rows[0][0].id
    state=NS(pool=[],runs=[],events=[],now=at(1),live=rows,contexts=rows,active=True,override_count=None)
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=actor))
    monkeypatch.setattr(history,'resource_actor',AsyncMock(return_value=actor))
    async def scalar(q):
        sql=str(q)
        entity=q.column_descriptions[0].get('entity')
        if entity is ImpactCheck:
            digest=q.compile().params.get('key_digest_1')
            return next((run for run in state.runs if run.key_digest==digest),None) if digest else state.runs[-1] if state.runs else None
        if entity is ImpactIssue: return state.pool[0] if state.pool else None
        if 'count(' in sql:
            if 'impact_issue_transitions' in sql: return len(state.events)
            if 'impact_issues' in sql: return len(state.pool)
            return state.override_count if state.override_count is not None else len(state.live)
        return state.now
    async def scalars(q):
        entity=q.column_descriptions[0].get('entity')
        return Mock(all=Mock(return_value={ImpactIssue:state.pool,ImpactTransition:state.events,SourceReviewRecord:v.rows}[entity]))
    async def execute(q):
        sql=str(q)
        if ' AS has_unfinished' in sql: result=[(*row,state.active) for row in state.contexts]
        elif 'IS DISTINCT FROM' in sql or ('EXISTS (SELECT shooting_sessions.id' in sql): result=state.live
        else: result=state.contexts
        return Mock(all=Mock(return_value=result))
    def add(row):
        if isinstance(row,ImpactIssue): state.pool.append(row)
        if isinstance(row,ImpactCheck): state.runs.append(row)
    v.session.scalar.side_effect=scalar; v.session.scalars.side_effect=scalars; v.session.execute.side_effect=execute
    v.session.add.side_effect=add
    v.session.add_all.side_effect=lambda rows:state.events.extend(rows)
    return NS(v=v,repo=repo,actor=actor,state=state,category=category,rows=rows)


def check(v,key=None,category=None):
    return asyncio.run(module.check(v.repo,v.v.user,v.v.script.crew_id,category or v.category,EmptyInput(),'a'*32,key or str(uuid4())))


@pytest.mark.parametrize('category',['breakdowns','storyboards','plans','call_sheets'])
def test_complete_check_records_scoped_issue_and_audit_without_modifying_business_data(monkeypatch,category):
    v=fixture(monkeypatch,category)
    source_lock=v.rows[0][0].lock_version
    result=check(v)
    assert result.detected_count==result.open_count==1 and result.resolved_count==0
    assert len(v.state.pool)==len(v.state.events)==1 and v.state.events[0].kind=='detected'
    issue=v.state.pool[0]
    assert issue.crew_id==v.v.script.crew_id and issue.target_id==v.rows[0][0].id
    assert issue.status=='open' and issue.lock_version==1 and issue.first_detected_at==at(1)
    assert v.rows[0][0].lock_version==source_lock
    assert {type(call.args[0]) for call in v.v.session.add.call_args_list}=={ImpactCheck,ImpactIssue,AuditLog}
    v.v.tx.__aexit__.assert_awaited_once_with(None,None,None)
    assert v.v.session.execute.call_args_list[0].args[0]._limit_clause is None


def test_unchanged_check_updates_observed_time_but_not_issue_version_or_duplicate_event(monkeypatch):
    v=fixture(monkeypatch); check(v); issue=v.state.pool[0]
    v.state.now=at(2); result=check(v)
    assert result.detected_count==result.updated_count==result.reopened_count==0
    assert issue.last_checked_at==at(2) and issue.first_detected_at==at(1) and issue.lock_version==1
    assert len(v.state.pool)==len(v.state.events)==1 and len(v.state.runs)==2


def test_working_epoch_change_records_new_basis_without_claiming_unobserved_recurrence(monkeypatch):
    v=fixture(monkeypatch); check(v)
    before=v.state.events[0].after_state
    v.v.script.working_revision+=1; v.state.now=at(2)
    result=check(v); issue=v.state.pool[0]
    assert result.updated_count==1 and result.reopened_count==0 and issue.lock_version==2
    assert before['evidence']['working_revision']==2
    assert v.state.events[-1].after_state['evidence']['working_revision']==3


def test_resolution_and_recurrence_reuse_issue_and_preserve_each_observed_basis(monkeypatch):
    v=fixture(monkeypatch); check(v); issue=v.state.pool[0]; issue_id=issue.id
    v.v.script.current_version_id=v.v.fixed.id; v.v.script.working_revision+=1
    v.state.live=[]; v.state.now=at(2)
    resolved=check(v)
    assert resolved.resolved_count==1 and resolved.open_count==0
    assert issue.status=='resolved' and issue.resolution_basis['reason']=='source_matches_working'
    resolved_event=v.state.events[-1]
    v.v.script.current_version_id=v.v.working.id; v.v.script.working_revision+=1
    v.state.live=v.rows; v.state.now=at(3)
    reopened=check(v)
    assert reopened.reopened_count==1 and issue.id==issue_id and issue.reopened_count==1
    assert issue.resolution_basis is None and issue.resolved_at is None and issue.first_detected_at==at(1)
    assert [event.kind for event in v.state.events]==['detected','resolved','reopened']
    assert resolved_event.after_state['resolution_basis']['reason']=='source_matches_working'


def test_source_review_resolution_links_actual_review_record_and_working_epoch(monkeypatch):
    v=fixture(monkeypatch); check(v)
    proof=SourceReviewRecord(id=uuid4(),breakdown_id=v.v.target.id,target_lock_version=2,source_script_version_id=v.v.fixed.id,
        working_script_version_id=v.v.working.id,working_revision=2,conclusion='still_applicable')
    v.v.rows.append(proof); v.state.live=[]; v.state.now=at(2)
    assert check(v).resolved_count==1
    issue=v.state.pool[0]
    assert issue.resolution_basis['reason']=='source_still_applicable' and issue.evidence['source_review_id']==str(proof.id)


@pytest.mark.parametrize('category,change,expected',[
    ('plans','withdrawn','plan_withdrawn'),('plans','superseded','plan_superseded'),('plans','finished','shooting_finished'),
    ('call_sheets','withdrawn','call_sheet_withdrawn'),('call_sheets','superseded','call_sheet_superseded'),('call_sheets','finished','shooting_finished'),
])
def test_formal_arrangement_history_distinguishes_cancellation_replacement_and_time_expiry(monkeypatch,category,change,expected):
    v=fixture(monkeypatch,category); check(v)
    target=v.rows[0][0]
    if change=='finished': v.state.active=False
    elif category=='plans':
        if change=='withdrawn': target.withdrawn_at=at(2)
        else: v.rows[0][1].current_published_version_id=uuid4()
    elif change=='withdrawn': target.validity_status='withdrawn'
    else: v.rows[0][1].current_published_version_id=uuid4()
    v.state.live=[]; v.state.now=at(2)
    assert check(v).resolved_count==1
    assert v.state.pool[0].resolution_basis['reason']==expected


def test_key_replay_does_not_scan_new_facts_or_duplicate_history(monkeypatch):
    v=fixture(monkeypatch); first=check(v,'same-key')
    v.state.live=[]; v.v.session.execute.reset_mock()
    second=check(v,'same-key')
    assert second==first and len(v.state.runs)==1 and len(v.state.events)==1
    v.v.session.execute.assert_not_awaited()
    with pytest.raises(AuthError) as caught: check(v,'same-key',category='plans')
    assert caught.value.code=='IDEMPOTENCY_KEY_REUSED'


def test_replay_still_requires_current_category_permissions_and_membership(monkeypatch):
    v=fixture(monkeypatch); check(v,'same-key')
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(v.actor,joined_at=at(3))))
    with pytest.raises(AuthError) as caught: check(v,'same-key')
    assert caught.value.code=='MEMBERSHIP_CHANGED'
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(v.actor,permissions=frozenset())))
    v.v.session.scalar.reset_mock()
    with pytest.raises(AuthError) as caught: check(v,'same-key')
    assert caught.value.status==403
    v.v.session.scalar.assert_not_awaited()


def test_oversized_or_failed_scan_does_not_resolve_unchecked_old_issues(monkeypatch):
    v=fixture(monkeypatch); check(v)
    v.v.session.add.reset_mock(); v.v.session.add_all.reset_mock()
    v.state.override_count=module.MAX_CHECK_TARGETS+1
    with pytest.raises(AuthError) as caught: check(v)
    assert caught.value.code=='CHANGE_CHECK_TOO_LARGE' and v.state.pool[0].status=='open'
    v.v.session.add.assert_not_called(); v.v.session.add_all.assert_not_called()
    v.state.override_count=None
    v.v.session.execute.side_effect=OperationalError('private SQL',{},Exception('scan failed'))
    with pytest.raises(OperationalError): check(v)
    assert v.state.pool[0].status=='open'


def test_commit_failure_is_not_reported_as_success(monkeypatch):
    v=fixture(monkeypatch); v.v.tx.__aexit__.side_effect=OperationalError('private SQL',{},Exception('commit failed'))
    with pytest.raises(OperationalError): check(v)


def test_history_is_read_only_and_returns_frozen_transition_not_current_target(monkeypatch):
    v=fixture(monkeypatch); check(v)
    v.v.session.add.reset_mock(); v.v.session.add_all.reset_mock(); v.v.session.flush.reset_mock()
    v.v.script.title='后续新标题'
    listing=asyncio.run(history.records(v.repo,v.v.user,v.v.script.crew_id,category=v.category))
    detail=asyncio.run(history.history(v.repo,v.v.user,v.v.script.crew_id,category=v.category,issue_id=v.state.pool[0].id))
    assert listing.latest_check.id==v.state.runs[0].id and listing.items[0].status=='open'
    assert detail.items[0].target.title.startswith('雨停之前') and detail.items[0].before_status is None
    v.v.session.add.assert_not_called(); v.v.session.add_all.assert_not_called(); v.v.session.flush.assert_not_awaited()


def test_unknown_history_is_hidden_and_query_binds_crew_category_and_issue(monkeypatch):
    v=fixture(monkeypatch)
    with pytest.raises(AuthError) as caught:
        asyncio.run(history.history(v.repo,v.v.user,v.v.script.crew_id,category=v.category,issue_id=uuid4()))
    assert caught.value.status==404
    sql=str(v.v.session.scalar.call_args.args[0])
    assert 'crew_id =' in sql and 'category =' in sql and 'impact_issues.id =' in sql


def test_migration_keeps_nonnullable_target_keys_and_scoped_foreign_keys(monkeypatch):
    monkeypatch.setattr(config_module,'load_settings',lambda:Settings(_env_file=None,database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    out=StringIO(); command.upgrade(Config('alembic.ini',output_buffer=out),'head',sql=True)
    sql=out.getvalue()
    assert '0024_impact_history' in sql and 'target_id UUID NOT NULL' in sql and 'UNIQUE (crew_id, category, target_id)' in sql
    assert 'FOREIGN KEY(crew_id, plan_version_id) REFERENCES shooting_plan_versions (crew_id, id)' in sql
    assert 'FOREIGN KEY(crew_id, issue_id) REFERENCES impact_issues (crew_id, id)' in sql
    assert 'UNIQUE (issue_id, check_id)' in sql


def test_check_http_needs_csrf_key_and_rejects_manual_resolution_body(crew_client,monkeypatch):
    client,_,user,crew=crew_client; v=fixture(monkeypatch)
    report=check(v)
    service=Mock(execute=AsyncMock(return_value=report)); client.app.state.changes=service
    path=f'/api/v1/crews/{crew.id}/change-issues/breakdowns/checks'
    assert client.post(path,json={}).status_code==422
    headers={'Idempotency-Key':str(uuid4())}
    assert client.post(path,json={},headers=headers).status_code==201
    assert service.execute.call_args.args[:3]==(user,'check',crew.id)
    assert client.post(path,json={'status':'resolved'},headers=headers).status_code==422
    client.headers.pop('X-CSRF-Token',None)
    assert client.post(path,json={},headers=headers).status_code==403
