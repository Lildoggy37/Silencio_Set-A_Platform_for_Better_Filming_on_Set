import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from silencio_set.accounts.errors import AuthError
from silencio_set.changes import repository as module
from silencio_set.changes.queries import allowed_categories, issue_query
from silencio_set.changes.service import ChangeService
from silencio_set.creative.models import SourceReviewRecord
from silencio_set.plans import repository as plans
from silencio_set.plans.models import ShootingPlan, PlanVersion
from silencio_set.call_sheets.models import CallSheet, CallSheetVersion
from test_source_reviews import fixture as source_fixture
from test_plans import fixture as plan_fixture, ZERO, at
from test_crews import ready_user, crew_client
from test_auth_api import auth_client


@pytest.mark.parametrize('permissions,expected', [
    ({'script.read','scene.read'}, ['breakdowns']),
    ({'script.read','storyboard.read'}, ['storyboards']),
    ({'plan.edit','call_sheet.publish'}, ['plans','call_sheets']),
    ({'plan.read_all'}, ['plans']),
    ({'scene.read','storyboard.read'}, []),
    ({'plan.read_related','call_sheet.read_related','member.authorize'}, []),
])
def test_categories_require_business_read_permissions_not_admin_or_related_only(permissions,expected):
    assert allowed_categories(permissions)==expected


@pytest.mark.parametrize('kind',['breakdowns','storyboards'])
def test_source_sql_excludes_only_exact_current_applicable_proof_and_keeps_missing_working_source(kind):
    crew=uuid4(); query,order=issue_query(kind,crew,ZERO)
    sql=str(query.order_by(*order).compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    assert str(crew) in sql and 'IS DISTINCT FROM scripts.current_version_id' in sql
    assert "IS DISTINCT FROM 'still_applicable'" in sql
    assert 'source_review_records.working_revision = scripts.working_revision' in sql
    assert 'source_review_records.target_lock_version =' in sql and "review_status = 'confirmed'" in sql
    assert 'FROM source_review_records \nWHERE' in sql  # Correlated to the outer script and target, not a second independent scan.
    assert 'LIMIT 1' in sql and 'source_review_records.reviewed_at DESC' in sql


def test_plan_sql_only_lists_current_flagged_versions_with_unfinished_sessions():
    query,_=issue_query('plans',uuid4(),at(1))
    sql=str(query.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    assert 'current_published_version_id = shooting_plan_versions.id' in sql
    assert 'jsonb_array_length(shooting_plan_versions.review_reasons) > 0' in sql
    assert 'shooting_sessions.transition_minutes' in sql and 'EXISTS (SELECT shooting_sessions.id' in sql
    assert '2026-11-02 01:00:00' in sql


def test_call_sheet_sql_excludes_withdrawn_or_superseded_and_respects_beijing_overnight_day():
    query,_=issue_query('call_sheets',uuid4(),ZERO)
    sql=str(query.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    assert "validity_status IN ('valid', 'review_required')" in sql
    assert 'current_published_version_id = call_sheet_versions.id' in sql
    assert 'IS DISTINCT FROM shooting_plan_versions.id' in sql
    assert "timezone('Asia/Shanghai', shooting_sessions.starts_at) AS DATE) = call_sheets.shooting_date" in sql


def fixture(monkeypatch,category='breakdowns'):
    v=source_fixture(monkeypatch,category if category in {'breakdowns','storyboards'} else 'breakdowns')
    actor=replace(v.actor,permissions=frozenset({'script.read','scene.read','storyboard.read','plan.edit','call_sheet.review'}))
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=actor))
    plan=ShootingPlan(id=uuid4(),crew_id=v.script.crew_id,name='首日安排',current_published_version_id=uuid4())
    version=PlanVersion(id=plan.current_published_version_id,plan_id=plan.id,version_no=2,review_reasons=['script_changed'])
    sheet=CallSheet(id=uuid4(),plan_id=plan.id,shooting_date=ZERO.date())
    sheet_version=CallSheetVersion(id=uuid4(),version_no=1,validity_status='review_required')
    rows=[(v.target,v.fixed,v.script)] if category in {'breakdowns','storyboards'} else [(version,plan)] if category=='plans' else [(sheet_version,sheet,version,plan)]
    async def scalar(q): return len(rows) if 'count(' in str(q) else at(1)
    v.session.scalar.side_effect=scalar
    v.session.execute.return_value=Mock(all=Mock(return_value=rows))
    return v, module.ChangeRepository(v.repo.sessions),actor,rows


@pytest.mark.parametrize('category',['breakdowns','storyboards','plans','call_sheets'])
def test_workbench_is_read_only_and_exposes_correct_scoped_navigation(monkeypatch,category):
    v,repo,_,rows=fixture(monkeypatch,category)
    result=asyncio.run(repo.list(v.user,v.script.crew_id,category=category))
    assert result.total==1 and result.observed_at==at(1) and result.category==category
    assert result.items[0].id==rows[0][0].id
    if category=='breakdowns': assert result.items[0].source_id==v.fixed.id
    elif category=='storyboards': assert result.items[0].source_id==v.target.scene_revision_id
    elif category=='call_sheets':
        item=result.items[0]
        assert item.container_id==rows[0][1].id and item.source_id==rows[0][2].id and item.current_plan_version_id==rows[0][3].current_published_version_id
    v.session.add.assert_not_called(); v.session.flush.assert_not_awaited()


def test_workbench_uses_same_source_review_decision_as_publication(monkeypatch):
    v,repo,_,_=fixture(monkeypatch)
    # Capture a valid source review and then present a needs-revision decision.
    v.rows.append(SourceReviewRecord(id=uuid4(),breakdown_id=v.target.id,target_lock_version=v.target.lock_version,
        source_script_version_id=v.fixed.id,working_script_version_id=v.script.current_version_id,
        working_revision=v.script.working_revision,conclusion='needs_revision'))
    result=asyncio.run(repo.list(v.user,v.script.crew_id,category='breakdowns'))
    assert result.items[0].status=='needs_revision' and result.items[0].reason_codes==['source_needs_revision']
    assert v.session.scalars.await_count==1  # One batch for the page, never per target.


def test_denied_category_does_not_reveal_count_or_query_data(monkeypatch):
    v,repo,actor,_=fixture(monkeypatch)
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(actor,permissions=frozenset({'plan.read_related'}))))
    with pytest.raises(AuthError) as caught: asyncio.run(repo.list(v.user,v.script.crew_id,category='plans'))
    assert caught.value.status==403
    v.session.scalar.assert_not_awaited(); v.session.execute.assert_not_awaited()


def test_counts_and_pagination_are_queried_before_loading_only_current_page_context(monkeypatch):
    v,repo,_,_=fixture(monkeypatch)
    v.session.scalar.side_effect=[ZERO,25]; v.session.execute.return_value=Mock(all=Mock(return_value=[]))
    result=asyncio.run(repo.list(v.user,v.script.crew_id,category='breakdowns',page=2,page_size=20))
    assert result.total==25 and result.items==[] and result.page==2
    statement=v.session.execute.call_args.args[0]
    assert statement._limit_clause.value==20 and statement._offset_clause.value==20


def test_service_sanitizes_failure_without_returning_empty_success():
    repo=Mock(list=AsyncMock(side_effect=OperationalError('private SQL',{},Exception('private credentials'))))
    audit=Mock(record_failure=AsyncMock())
    with pytest.raises(AuthError) as caught: asyncio.run(ChangeService(repo,audit).list(ready_user(),uuid4(),'a'*32,category='plans'))
    assert caught.value.status==503 and 'private' not in caught.value.public_message
    audit.record_failure.assert_awaited_once()


def test_plan_metadata_is_accessible_without_being_on_first_list_page(monkeypatch):
    repo,_,_,_,plan,_,user=plan_fixture(monkeypatch)
    result=asyncio.run(repo.get_plan(user,plan.crew_id,plan_id=plan.id))
    assert result.id==plan.id


@pytest.mark.parametrize('related',[True,False])
def test_plan_metadata_related_access_requires_current_membership_epoch(monkeypatch,related):
    repo,session,_,actor,plan,_,user=plan_fixture(monkeypatch)
    monkeypatch.setattr(plans,'resource_actor',AsyncMock(return_value=replace(actor,permissions=frozenset({'plan.read_related'}))))
    session.scalar.side_effect=[plan,related]
    if related: assert asyncio.run(repo.get_plan(user,plan.crew_id,plan_id=plan.id)).id==plan.id
    else:
        with pytest.raises(AuthError) as caught: asyncio.run(repo.get_plan(user,plan.crew_id,plan_id=plan.id))
        assert caught.value.status==404
    sql=str(session.scalar.call_args.args[0])
    assert 'membership_joined_at' in sql and 'plan_version_id' in sql


def test_http_workbench_is_get_only_and_rejects_unbounded_pages(crew_client,monkeypatch):
    client,_,user,crew=crew_client
    v,repo,_,_=fixture(monkeypatch,'plans')
    result=asyncio.run(repo.list(v.user,v.script.crew_id,category='plans'))
    service=Mock(list=AsyncMock(return_value=result)); client.app.state.changes=service
    path=f'/api/v1/crews/{crew.id}/change-issues/plans'
    client.headers.pop('X-CSRF-Token',None)
    assert client.get(path).status_code==200
    assert service.list.call_args.args[:2]==(user,crew.id)
    assert client.get(path+'?page_size=21').status_code==422
    assert client.get(path.replace('/plans','/members')).status_code==422
    assert client.post(path,json={}).status_code==405
