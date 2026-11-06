import asyncio
from dataclasses import replace
from hashlib import sha256
from io import BytesIO, StringIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest
from sqlalchemy.exc import OperationalError
from starlette.datastructures import UploadFile

from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.scripts import extractor, repository as module, service as service_module
from silencio_set.scripts.extractor import extract_document, parse_in_process, ExtractionFailure
from silencio_set.scripts.models import ScriptSourceFile, ScriptVersion, ScriptReceipt, ScriptParagraph
from silencio_set.scripts.schemas import ExtractedDocument, FileImportIdentity, ScriptText, ScriptResult, ScriptView
from silencio_set.scripts.service import ScriptService
from silencio_set.infrastructure.private_files import PrivateFileStorage
from test_stock_scripts import script_fixture, hour
from test_auth_api import auth_client
from test_crews import crew_client


def docx_bytes():
    document=Document()
    document.add_paragraph('第一场：教学楼')
    table=document.add_table(rows=1, cols=2)
    table.cell(0,0).text='角色：林雨'
    table.cell(0,1).text='台词：雨停了。'
    document.add_paragraph('第二场：操场')
    output=BytesIO(); document.save(output)
    return output.getvalue()


def pdf_bytes(*, encrypted=False, blank=False, pages=2):
    writer=PdfWriter()
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    for index in range(pages):
        page=writer.add_blank_page(width=300,height=300)
        if not blank and index==0:
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream=DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 10 250 Td (SCENE ONE) Tj ET')
            page[NameObject('/Contents')]=writer._add_object(stream)
    if encrypted: writer.encrypt('private')
    output=BytesIO(); writer.write(output)
    return output.getvalue()


def test_docx_preserves_chinese_body_table_order_without_invented_pages():
    result=extract_document(docx_bytes(),'docx')
    assert [row.text for row in result.paragraphs]==['第一场：教学楼','角色：林雨','台词：雨停了。','第二场：操场']
    assert all(row.source_page is None for row in result.paragraphs) and result.page_count is None
    assert result.notes


def test_pdf_preserves_page_references_and_warns_about_pages_without_text():
    result=extract_document(pdf_bytes(),'pdf')
    assert result.page_count==2 and result.paragraphs[0].text=='SCENE ONE' and result.paragraphs[0].source_page==1
    assert any('2' in note and '未提取到文字' in note for note in result.notes)


@pytest.mark.parametrize('data,format,code',[(b'','txt','DOCUMENT_EMPTY'),(b'not pdf','pdf','DOCUMENT_FORMAT_MISMATCH'),
    (b'not docx','docx','DOCUMENT_FORMAT_MISMATCH'),(b'\xff\xfe','txt','DOCUMENT_ENCODING'),
    (b'x'*100001,'txt','DOCUMENT_TEXT_TOO_LARGE'),(b'x','exe','DOCUMENT_TYPE_UNSUPPORTED')], ids=['empty','wrong-pdf','wrong-docx','encoding','text-limit','unsupported'])
def test_invalid_or_oversized_document_has_explicit_failure(data,format,code):
    with pytest.raises(ExtractionFailure) as caught: extract_document(data,format)
    assert str(caught.value)==code


@pytest.mark.parametrize('options,code',[(dict(encrypted=True),'DOCUMENT_ENCRYPTED'),(dict(blank=True),'DOCUMENT_NO_TEXT'),(dict(pages=101),'DOCUMENT_PAGE_LIMIT')])
def test_encrypted_scanned_or_too_many_pdf_pages_do_not_create_success(options,code):
    with pytest.raises(ExtractionFailure) as caught: extract_document(pdf_bytes(**options),'pdf')
    assert str(caught.value)==code


@pytest.mark.parametrize('format',['docx','pdf','txt'])
def test_actual_isolated_process_returns_only_extracted_paragraphs(format):
    data=docx_bytes() if format=='docx' else pdf_bytes() if format=='pdf' else '第一场\n\n台词：你好。'.encode('utf-8-sig')
    result=parse_in_process(data,format)
    assert result.paragraphs and result.paragraphs[0].text


def test_corrupt_docx_and_child_timeout_do_not_expose_parser_details(monkeypatch):
    with pytest.raises(AuthError) as caught: parse_in_process(b'PK\x03\x04broken document','docx')
    assert caught.value.code=='DOCUMENT_INVALID'
    import subprocess
    monkeypatch.setattr(extractor.subprocess,'run',Mock(side_effect=subprocess.TimeoutExpired('private path',30)))
    with pytest.raises(AuthError) as caught: parse_in_process(b'text','txt')
    assert caught.value.code=='DOCUMENT_PARSE_TIMEOUT' and 'private' not in str(caught.value)


def imported_fixture(monkeypatch):
    repo,session,tx,actor,script,version=script_fixture(monkeypatch)
    data='第一场\n你好。'.encode()
    body=FileImportIdentity(expected_lock_version=1,original_name='剧本.txt',file_format='txt',byte_size=len(data),sha256=sha256(data).hexdigest())
    parsed=extract_document(data,'txt')
    source=ScriptSourceFile(id=uuid4(),crew_id=script.crew_id,script_id=script.id,original_name=body.original_name,file_format='txt',byte_size=len(data),sha256=body.sha256,
        storage_key=f'{script.crew_id.hex}/{uuid4().hex}.txt',page_count=None,extraction_notes=[])
    old=session.scalar.side_effect
    async def scalar(query):
        if query.column_descriptions[0].get('entity') is ScriptSourceFile: return source
        return await old(query)
    session.scalar.side_effect=scalar
    user=uuid4()
    def run(): return asyncio.run(repo.mutate(user,script.crew_id,'import_file',body,'a'*32,idempotency_key='import-key',script_id=script.id,source_file=source,extracted=parsed))
    return NS(repo=repo,session=session,tx=tx,actor=actor,script=script,source=source,body=body,parsed=parsed,run=run,user=user)


def test_import_persists_original_and_pending_version_but_never_changes_working_pointer(monkeypatch):
    v=imported_fixture(monkeypatch)
    result=v.run()
    version=next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0],ScriptVersion))
    assert version.source_file_id==v.source.id and version.status=='pending_check'
    assert v.script.current_version_id is None and v.script.lock_version==2
    assert result.version.source_file.original_name=='剧本.txt'
    assert 'storage_key' not in result.model_dump_json() and 'sha256' not in result.model_dump_json()
    assert v.source.created_by==v.actor.member_id
    rows=v.session.add_all.call_args.args[0]
    assert [row.paragraph_no for row in rows]==[1,2] and all(row.source_page is None for row in rows)


def test_successful_import_replays_before_parsing_or_stale_script_check(monkeypatch):
    v=imported_fixture(monkeypatch); first=v.run()
    receipt=next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0],ScriptReceipt))
    v.session.scalar.side_effect=None; v.session.scalar.return_value=receipt
    assert asyncio.run(v.repo.prepare_import(v.user,v.script.crew_id,v.script.id,v.body,'import-key'))==first
    receipt.membership_joined_at=hour(1)
    with pytest.raises(AuthError) as caught: asyncio.run(v.repo.prepare_import(v.user,v.script.crew_id,v.script.id,v.body,'import-key'))
    assert caught.value.code=='MEMBERSHIP_CHANGED'


def test_import_key_cannot_be_reused_with_different_file_bytes(monkeypatch):
    v=imported_fixture(monkeypatch); v.run()
    receipt=next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0],ScriptReceipt))
    v.session.scalar.side_effect=None; v.session.scalar.return_value=receipt
    changed=v.body.model_copy(update={'sha256':'0'*64})
    with pytest.raises(AuthError) as caught: asyncio.run(v.repo.prepare_import(v.user,v.script.crew_id,v.script.id,changed,'import-key'))
    assert caught.value.code=='IDEMPOTENCY_KEY_REUSED'


def test_final_import_write_rechecks_permissions_after_extraction(monkeypatch):
    v=imported_fixture(monkeypatch)
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(v.actor,permissions=frozenset({'script.read'}))))
    with pytest.raises(AuthError) as caught: v.run()
    assert caught.value.code=='FORBIDDEN'
    v.session.add.assert_not_called()


def test_import_commit_failure_is_not_reported_as_success(monkeypatch):
    v=imported_fixture(monkeypatch)
    v.tx.__aexit__.side_effect=OperationalError('private SQL',{},Exception('lost commit'))
    with pytest.raises(OperationalError): v.run()


def test_correcting_imported_text_keeps_original_but_clears_old_page_references(monkeypatch):
    v=imported_fixture(monkeypatch)
    original=v.session.scalar.side_effect
    version=ScriptVersion(id=uuid4(),crew_id=v.script.crew_id,script_id=v.script.id,status='pending_check',lock_version=1,version_no=1,source_file_id=v.source.id,confirmed_by=None,confirmed_at=None)
    async def scalar(query):
        if query.column_descriptions[0].get('entity') is ScriptVersion: return version
        return await original(query)
    v.session.scalar.side_effect=scalar
    result=asyncio.run(v.repo.mutate(v.user,v.script.crew_id,'correct',ScriptText(expected_lock_version=1,text='人工修正正文'),'a'*32,idempotency_key='correction',script_id=v.script.id,version_id=version.id))
    assert result.version.source_file.id==v.source.id
    assert all(row.source_page is None for row in v.session.add_all.call_args.args[0])


def test_service_does_not_parse_or_write_files_when_preflight_denies(monkeypatch):
    repo=Mock(prepare_import=AsyncMock(side_effect=AuthError(403,'FORBIDDEN','Denied')))
    parser=Mock(); monkeypatch.setattr(service_module,'parse_in_process',parser)
    storage=Mock(); service=ScriptService(repo,Mock(record_failure=AsyncMock()),storage)
    user=NS(id=uuid4(),status='active',display_name='导演',profile_completed=True)
    with pytest.raises(AuthError): asyncio.run(service.execute(user,'import_file','a'*32,crew_id=uuid4(),script_id=uuid4(),upload=UploadFile(BytesIO(b'text'),filename='script.txt'),expected_lock_version=1,idempotency_key='key'))
    parser.assert_not_called(); storage.put.assert_not_called()


def test_service_import_writes_private_original_and_returns_real_pending_version(monkeypatch,tmp_path):
    v=imported_fixture(monkeypatch)
    parser=Mock(return_value=v.parsed); monkeypatch.setattr(service_module,'parse_in_process',parser)
    original=v.session.scalar.side_effect
    async def scalar(query):
        if query.column_descriptions[0].get('entity') is ScriptSourceFile:
            return next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0],ScriptSourceFile))
        return await original(query)
    v.session.scalar.side_effect=scalar
    storage=PrivateFileStorage(tmp_path)
    service=ScriptService(v.repo,Mock(record_failure=AsyncMock()),storage)
    data='第一场\n你好。'.encode()
    user=NS(id=v.user,status='active',profile_completed=True)
    result=asyncio.run(service.execute(user,'import_file','a'*32,crew_id=v.script.crew_id,script_id=v.script.id,
        upload=UploadFile(BytesIO(data),filename='C:\\fakepath\\剧本.txt'),expected_lock_version=1,idempotency_key='source-file'))
    assert result.version.status=='pending_check' and result.script.current_version_id is None
    assert result.version.source_file.original_name=='剧本.txt'
    files=list(tmp_path.rglob('*.txt'))
    assert len(files)==1 and files[0].read_bytes()==data
    parser.assert_called_once_with(data,'txt')


def test_storage_never_uses_client_filename_as_path_and_remains_private(tmp_path):
    storage=PrivateFileStorage(tmp_path)
    key=f'{uuid4().hex}/{uuid4().hex}.pdf'
    path=storage.put(key,b'%PDF-example')
    assert path.is_relative_to(tmp_path) and path.read_bytes()==b'%PDF-example'
    with pytest.raises(ValueError): storage.path('../outside.pdf')


def test_multipart_import_csrf_body_types_and_private_original_download(crew_client,tmp_path):
    client,audits,user,crew=crew_client
    script_id,version_id=uuid4(),uuid4()
    script=NS(id=script_id,crew_id=crew.id,title='剧本',current_version_id=None,lock_version=1,created_at=hour(0),updated_at=hour(0))
    repo=Mock(prepare_import=AsyncMock(return_value=ScriptResult(script=ScriptView.model_validate(script))),source_file=AsyncMock())
    storage=PrivateFileStorage(tmp_path)
    client.app.state.scripts=ScriptService(repo,audits,storage)
    url=f'/api/v1/crews/{crew.id}/scripts/{script_id}/imports'
    headers={'Idempotency-Key':str(uuid4())}
    response=client.post(url,files={'file':('剧本.txt',b'plain text')},data={'expected_lock_version':'1'},headers=headers)
    assert response.status_code==201
    assert repo.prepare_import.await_args.args[0]==user.id
    denied=client.post(url,files={'file':('script.txt',b'text')},data={'expected_lock_version':'1'},headers=headers|{'X-CSRF-Token':'bad'})
    assert denied.status_code==403
    assert client.post(url,json={'file':'fake'},headers=headers).status_code==415
    assert client.post(f'/api/v1/crews/{crew.id}/scripts/{script_id}/versions',files={'file':('test.txt',b'text')},headers=headers).status_code==415
    key=f'{crew.id.hex}/{uuid4().hex}.txt'; storage.put(key,b'private original')
    repo.source_file.return_value=NS(storage_key=key,original_name='原始剧本.txt')
    source_url=f'/api/v1/crews/{crew.id}/scripts/{script_id}/versions/{version_id}/source-file'
    downloaded=client.get(source_url)
    assert downloaded.content==b'private original' and downloaded.headers['cache-control']=='no-store'
    assert downloaded.headers['content-disposition'].startswith('attachment;')
    assert repo.source_file.await_args.args==(user.id,crew.id)
    repo.source_file.side_effect=AuthError(404,'NOT_FOUND','Hidden')
    assert client.get(source_url).status_code==404


def test_source_download_query_is_scoped_to_crew_script_and_version(monkeypatch):
    v=imported_fixture(monkeypatch)
    v.session.scalar.side_effect=None; v.session.scalar.return_value=None
    version_id=uuid4()
    with pytest.raises(AuthError): asyncio.run(v.repo.source_file(v.user,v.script.crew_id,v.script.id,version_id))
    query=v.session.scalar.call_args.args[0]
    compiled=query.compile()
    assert {v.script.crew_id,v.script.id,version_id}<=set(compiled.params.values())
    assert 'script_versions.source_file_id = script_source_files.id' in str(query)


def test_import_migration_preserves_same_script_provenance(monkeypatch):
    monkeypatch.setattr(config_module,'load_settings',lambda:Settings(_env_file=None,database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    output=StringIO(); command.upgrade(Config('alembic.ini',output_buffer=output),'head',sql=True)
    sql=output.getvalue()
    assert '0019_script_imports' in sql and 'CREATE TABLE script_source_files' in sql
    assert 'FOREIGN KEY(crew_id, script_id, source_file_id) REFERENCES script_source_files (crew_id, script_id, id)' in sql
    assert 'byte_size BETWEEN 1 AND 20971520' in sql
