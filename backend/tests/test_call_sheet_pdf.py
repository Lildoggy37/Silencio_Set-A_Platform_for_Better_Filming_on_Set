import asyncio
from dataclasses import replace
from io import BytesIO
import subprocess
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from pypdf import PdfReader
import pytest

from silencio_set.accounts.errors import AuthError
from silencio_set.call_sheets import repository as repository_module, service as service_module, pdf
from silencio_set.call_sheets.service import CallSheetService
from call_sheet_samples import sample_export
from test_call_sheets import fixture
from test_crews import crew_client
from test_auth_api import auth_client


def test_pdf_embeds_chinese_uses_snapshot_and_fits_typical_day_on_a4():
    snapshot=sample_export()
    reader=PdfReader(BytesIO(pdf.render_call_sheet_pdf(snapshot)))
    assert len(reader.pages)==1
    page=reader.pages[0]; text=page.extract_text()
    assert '雨停之前（虚构示例）' in text and '教学楼东侧入口' in text and '便携收音套装' in text
    assert '2026-11-03 09:00:00' in text and '当前有效 - 已发布' in text and str(snapshot.version.source_plan_version_id) in text
    assert '离线文件不会自动更新' in text
    assert round(float(page.mediabox.width))==595 and round(float(page.mediabox.height))==842
    fonts=[font.get_object() for font in page['/Resources']['/Font'].get_object().values()]
    assert any('/FontDescriptor' in font and '/FontFile2' in font['/FontDescriptor'].get_object() for font in fonts)


@pytest.mark.parametrize('change,label',[
    ('draft','草稿 - 未发布'),('pending_review','待审核 - 未发布'),('approved','已审核 - 未发布'),
    ('withdrawn','已撤回 - 不作为拍摄安排'),('superseded','已被新版替代 - 历史记录'),
    ('review_required','来源待复核 - 请核对最新安排'),('stale-source','来源待复核 - 请核对最新安排'),
    ('changed-pointer','已被新版替代 - 历史记录')])
def test_export_never_labels_unpublished_or_invalid_versions_as_effective(change,label):
    snapshot=sample_export(); version=snapshot.version
    if change in {'draft','pending_review','approved'}: version.status=change; version.validity_status=None
    elif change=='stale-source': version.source_current=False
    elif change=='changed-pointer': version.call_sheet.current_published_version_id=uuid4()
    else: version.validity_status=change
    text=PdfReader(BytesIO(pdf.render_call_sheet_pdf(snapshot))).pages[0].extract_text()
    assert label in text and '不作为当前有效拍摄安排' in text
    assert '当前有效 - 已发布' not in text


def test_long_single_row_splits_without_losing_last_member_or_repeated_state():
    snapshot=sample_export()
    snapshot.version.validity_status='withdrawn'
    snapshot.version.arrangement_snapshot['sessions'][0]['members']=[{'display_name':f'工作人员{index:03d}（分组筹备）'} for index in range(240)]
    reader=PdfReader(BytesIO(pdf.render_call_sheet_pdf(snapshot)))
    assert len(reader.pages)>1
    assert '工作人员000' in reader.pages[0].extract_text()
    text=''.join(''.join(page.extract_text().split()) for page in reader.pages)
    assert '工作人员239（分组筹备）' in text and '雨停之前' in text and '未接来电' in text
    for index,page in enumerate(reader.pages,1):
        rendered=page.extract_text()
        assert '已撤回 - 不作为拍摄安排' in rendered and f'第 {index} 页' in rendered
        assert '导出 2026-11-03 08:15:00' in rendered


def test_user_markup_remains_literal_text_and_unsupported_characters_fail_explicitly():
    snapshot=sample_export()
    snapshot.version.notes='<img src="https://invalid.example/pixel"> & 请保持安静。'
    reader=PdfReader(BytesIO(pdf.render_call_sheet_pdf(snapshot)))
    assert '<img src=' in ''.join(page.extract_text() for page in reader.pages)
    assert '/OpenAction' not in reader.trailer['/Root'] and all('/Annots' not in page for page in reader.pages)
    snapshot.version.notes='暂不支持的字符：🧑'
    with pytest.raises(pdf.PdfTextUnsupported): pdf.render_call_sheet_pdf(snapshot)


@pytest.mark.parametrize('permissions', [frozenset({'call_sheet.edit'}),frozenset({'call_sheet.export'})])
def test_export_requires_independent_export_permission_and_source_read_access(monkeypatch,permissions):
    v=fixture(monkeypatch)
    monkeypatch.setattr(repository_module,'resource_actor',AsyncMock(return_value=replace(v.actor,permissions=permissions)))
    with pytest.raises(AuthError) as caught: asyncio.run(v.repo.export_snapshot(v.user,v.crew_id,v.version.id,1))
    assert caught.value.code=='FORBIDDEN'
    v.session.add.assert_not_called()


def test_export_captures_current_data_under_coordinator_and_rejects_stale_lock(monkeypatch):
    v=fixture(monkeypatch)
    monkeypatch.setattr(repository_module,'resource_actor',AsyncMock(return_value=replace(v.actor,permissions=v.actor.permissions|{'call_sheet.export'})))
    result=asyncio.run(v.repo.export_snapshot(v.user,v.crew_id,v.version.id,1))
    assert result.version.id==v.version.id and result.version.arrangement_snapshot==v.version.arrangement_snapshot
    assert result.exported_at==v.actor.joined_at
    with pytest.raises(AuthError) as caught: asyncio.run(v.repo.export_snapshot(v.user,v.crew_id,v.version.id,2))
    assert caught.value.code=='VERSION_CONFLICT'
    v.session.add.assert_not_called(); v.session.add_all.assert_not_called()


@pytest.mark.parametrize('visible,published,allowed',[(False,True,False),(True,False,False),(True,True,True)])
def test_export_grant_does_not_bypass_related_recipient_epoch(monkeypatch,visible,published,allowed):
    v=fixture(monkeypatch)
    monkeypatch.setattr(repository_module,'resource_actor',AsyncMock(return_value=replace(v.actor,permissions=frozenset({'call_sheet.export','call_sheet.read_related'}))))
    if published: v.version.status='published'; v.version.validity_status='valid'; v.state.rows[0].required_at_publish=True
    original=v.session.scalar.side_effect
    async def scalar(query):
        if query.column_descriptions[0].get('entity') is None and 'EXISTS' in str(query): return visible
        return await original(query)
    v.session.scalar.side_effect=scalar
    operation=v.repo.export_snapshot(v.user,v.crew_id,v.version.id,1)
    if allowed: assert asyncio.run(operation).version.id==v.version.id
    else:
        with pytest.raises(AuthError) as caught: asyncio.run(operation)
        assert caught.value.code=='NOT_FOUND'


def test_export_timeout_is_explicit_and_never_returns_old_bytes(monkeypatch):
    monkeypatch.setattr(pdf.subprocess,'run',Mock(side_effect=subprocess.TimeoutExpired('private path',30)))
    with pytest.raises(AuthError) as caught: pdf.render_in_process(sample_export())
    assert caught.value.code=='PDF_EXPORT_FAILED'


def test_export_checks_authority_before_starting_renderer(monkeypatch):
    renderer=Mock(); monkeypatch.setattr(service_module,'render_in_process',renderer)
    repo=Mock(export_snapshot=AsyncMock(side_effect=AuthError(403,'FORBIDDEN','Denied')))
    audits=Mock(record_failure=AsyncMock())
    from types import SimpleNamespace
    user=SimpleNamespace(id=uuid4(),status='active',profile_completed=True)
    with pytest.raises(AuthError): asyncio.run(CallSheetService(repo,audits).execute(user,'export_pdf','a'*32,crew_id=uuid4(),version_id=uuid4(),expected_lock_version=1))
    renderer.assert_not_called()


def test_http_export_is_private_pdf_for_exact_version_and_errors_stay_json(crew_client):
    client,audits,user,crew=crew_client
    snapshot=sample_export()
    repo=Mock(export_snapshot=AsyncMock(return_value=snapshot))
    client.app.state.call_sheets=CallSheetService(repo,audits)
    path=f'/api/v1/crews/{crew.id}/call-sheet-versions/{snapshot.version.id}/export.pdf'
    assert client.get(path).status_code==422
    response=client.get(path+'?expected_lock_version=4')
    assert response.status_code==200 and response.headers['content-type']=='application/pdf'
    assert response.headers['cache-control']=='no-store' and response.headers['content-disposition'].startswith('attachment;')
    assert response.content.startswith(b'%PDF-')
    repo.export_snapshot.assert_awaited_once_with(user.id,crew.id,version_id=snapshot.version.id,expected_lock_version=4)
    repo.export_snapshot.side_effect=AuthError(403,'FORBIDDEN','Denied')
    denied=client.get(path+'?expected_lock_version=4')
    assert denied.status_code==403 and denied.headers['content-type'].startswith('application/json')
