import asyncio
from dataclasses import replace
from hashlib import sha256
from io import BytesIO, StringIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from PIL import Image, PngImagePlugin
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from starlette.datastructures import UploadFile
import pytest

from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.infrastructure.private_files import PrivateFileStorage
from silencio_set.locations import images, repository as module, service as service_module
from silencio_set.locations.models import LocationDetails, LocationPhoto
from silencio_set.locations.repository import LocationRepository
from silencio_set.locations.schemas import LocationDetailsEdit, PhotoDescriptionEdit, PhotoUploadIdentity
from silencio_set.locations.service import LocationService
from silencio_set.resources.models import Resource, ResourceReceipt
from silencio_set.tasks.schemas import VersionInput
from test_account_repository import fake_session_factory
from test_resources import EPOCH, hour, setup_repository
from test_auth_api import auth_client
from test_crews import crew_client


def image_bytes(format='JPEG',size=(80,40),exif=None):
    source=Image.new('RGB',size,'#ea3025'); output=BytesIO()
    options={'exif':exif} if exif else {}
    source.save(output,format,**options);source.close()
    return output.getvalue()


def test_jpeg_orientation_and_location_metadata_are_removed_from_full_and_preview():
    exif=Image.Exif();exif[274]=6;exif[271]='Camera-Make';exif[315]='Private Artist'
    exif[34853]={1:'N',2:(1.0,2.0,3.0),3:'E',4:(4.0,5.0,6.0)}
    result=images.normalize_photo(image_bytes(exif=exif))
    assert (result.width_px,result.height_px)==(40,80) and result.file_format=='jpeg'
    for data in (result.content,result.preview):
        with Image.open(BytesIO(data)) as image:
            assert image.size==(40,80) and not image.getexif()
            assert not {'exif','icc_profile','xmp','comment'} & image.info.keys()
    assert b'Private Artist' not in result.content and b'Camera-Make' not in result.content


def test_png_metadata_is_stripped_and_transparency_is_preserved():
    source=Image.new('RGBA',(60,30),(20,50,100,80));output=BytesIO()
    info=PngImagePlugin.PngInfo();info.add_text('Location','Private GPS');info.add_itxt('XML:com.adobe.xmp','Private XMP')
    source.save(output,'PNG',pnginfo=info)
    result=images.normalize_photo(output.getvalue())
    with Image.open(BytesIO(result.content)) as normalized:
        assert normalized.mode=='RGBA' and normalized.getpixel((0,0))==(20,50,100,80)
        assert 'Location' not in normalized.info and 'XML:com.adobe.xmp' not in normalized.info


def test_large_photo_has_small_preview_without_resizing_full_image():
    result=images.normalize_photo(image_bytes('PNG',(1800,900)))
    with Image.open(BytesIO(result.content)) as full,Image.open(BytesIO(result.preview)) as preview:
        assert full.size==(1800,900) and preview.size==(1280,640) and preview.format=='WEBP'


@pytest.mark.parametrize('format',['JPEG','PNG','WEBP'])
def test_actual_photo_subprocess_returns_decodable_normalized_files(format):
    result=images.normalize_in_process(image_bytes(format))
    assert result.file_format==format.lower()
    for data in (result.content,result.preview):
        with Image.open(BytesIO(data)) as image: image.load();assert image.size==(80,40)


@pytest.mark.parametrize('payload,code,status',[(b'','PHOTO_EMPTY',422),(b'<svg xmlns="http://www.w3.org/2000/svg"/>','PHOTO_TYPE_UNSUPPORTED',415),
    (b'GIF89a','PHOTO_TYPE_UNSUPPORTED',415),(b'\x89PNG\r\n\x1a\nbroken','PHOTO_INVALID',422)],ids=['empty','svg','gif','corrupt-png'])
def test_unsupported_or_corrupt_images_are_not_accepted(payload,code,status):
    with pytest.raises(AuthError) as caught: images.normalize_in_process(payload)
    assert caught.value.code==code and caught.value.status==status


@pytest.mark.parametrize('format',['PNG','WEBP'])
def test_animated_supported_container_is_rejected(format):
    frames=[Image.new('RGB',(20,20),color) for color in ('red','blue')]
    output=BytesIO();frames[0].save(output,format,save_all=True,append_images=frames[1:],duration=100,loop=0)
    with pytest.raises(images.PhotoProcessingError) as caught: images.normalize_photo(output.getvalue())
    assert str(caught.value)=='PHOTO_ANIMATED'


def test_pixel_limit_is_checked_before_image_decode(monkeypatch):
    monkeypatch.setattr(images,'MAX_PIXELS',100)
    with pytest.raises(images.PhotoProcessingError) as caught: images.normalize_photo(image_bytes(size=(11,10)))
    assert str(caught.value)=='PHOTO_PIXEL_LIMIT'


def fixture(monkeypatch):
    _,_,_,person,actor,resource,_=setup_repository(monkeypatch)
    factory,session,tx=fake_session_factory()
    resource.resource_type='location';resource.name='教学楼走廊'
    state=NS(details=None,photos=[],receipt=None,scope=[])
    async def scalar(query):
        if 'count(' in str(query): return len([row for row in state.photos if row.removed_at is None])
        entity=query.column_descriptions[0].get('entity')
        if entity is Resource: return resource
        if entity is ResourceReceipt: return state.receipt
        if entity is LocationDetails: return state.details
        if entity is LocationPhoto: return next((row for row in state.photos if row.removed_at is None),None)
        return hour(0)
    async def scalars(query):
        rows=[row for row in state.photos if row.removed_at is None] if query.column_descriptions[0].get('entity') is LocationPhoto else state.scope
        return Mock(all=Mock(return_value=rows))
    def add(row):
        if isinstance(row,LocationDetails): state.details=row
        elif isinstance(row,LocationPhoto): state.photos.append(row)
    session.scalar.side_effect=scalar;session.scalars.side_effect=scalars;session.add.side_effect=add
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=actor))
    repo=LocationRepository(factory)
    def run(action,body,photo_id=None,new_photo=None):
        return asyncio.run(repo.mutate(person.user_id,person.crew_id,resource.id,action,body,'a'*32,
            idempotency_key='location-key',photo_id=photo_id,new_photo=new_photo))
    return NS(repo=repo,session=session,tx=tx,state=state,actor=actor,resource=resource,person=person,run=run)


def photo(v):
    return LocationPhoto(id=uuid4(),crew_id=v.person.crew_id,location_resource_id=v.resource.id,created_by=v.actor.member_id,
        description='入口与可用空间',file_format='jpeg',byte_size=100,width_px=80,height_px=40,sha256='1'*64,
        storage_key=f'{uuid4().hex}.jpeg',preview_storage_key=f'{uuid4().hex}.webp',preview_byte_size=80,created_at=hour(0),removed_at=None,removed_by=None)


def test_director_can_read_but_cannot_modify_location_without_management_scope(monkeypatch):
    v=fixture(monkeypatch)
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(v.actor,roles=frozenset({'director'}),permissions=frozenset({'resource.read'}))))
    result=asyncio.run(v.repo.get(v.person.user_id,v.person.crew_id,v.resource.id))
    assert result.details.address is None and result.allowed_actions==[]
    with pytest.raises(AuthError) as caught: v.run('edit_details',LocationDetailsEdit(expected_lock_version=1,address='教学楼'))
    assert caught.value.code=='FORBIDDEN'
    v.session.add.assert_not_called()


@pytest.mark.parametrize('scope,allowed',[([],False),(['resource.manage'],True)])
def test_delegated_editor_requires_matching_resource_scope(monkeypatch,scope,allowed):
    v=fixture(monkeypatch);v.state.scope=scope
    monkeypatch.setattr(module,'resource_actor',AsyncMock(return_value=replace(v.actor,roles=frozenset({'staff'}))))
    operation=v.repo.prepare_upload(v.person.user_id,v.person.crew_id,v.resource.id,
        PhotoUploadIdentity(expected_lock_version=1,description=None,source_sha256='1'*64),'key')
    if allowed: assert asyncio.run(operation) is None
    else:
        with pytest.raises(AuthError) as caught: asyncio.run(operation)
        assert caught.value.code=='FORBIDDEN'
    sql=str(v.session.scalars.call_args.args[0])
    assert 'membership_joined_at' in sql


def test_details_and_photo_changes_share_resource_version_and_keep_receipt_and_audit(monkeypatch):
    v=fixture(monkeypatch)
    result=v.run('edit_details',LocationDetailsEdit(expected_lock_version=1,address='教学楼东入口',shooting_constraints='上课期间保持安静'))
    assert result.resource_lock_version==2 and result.details.address=='教学楼东入口'
    assert v.resource.lock_version==2
    saved=[call.args[0] for call in v.session.add.call_args_list]
    assert any(isinstance(row,ResourceReceipt) for row in saved)
    with pytest.raises(AuthError) as caught: v.run('edit_details',LocationDetailsEdit(expected_lock_version=1,address='过期输入'))
    assert caught.value.code=='VERSION_CONFLICT'


def test_upload_then_remove_hides_photo_without_deleting_history_or_disk(monkeypatch):
    v=fixture(monkeypatch);row=photo(v)
    added=v.run('upload_photo',PhotoUploadIdentity(expected_lock_version=1,description=row.description,source_sha256='2'*64),new_photo=row)
    assert len(added.photos)==1 and added.photos[0].width_px==80 and v.resource.lock_version==2
    assert 'storage_key' not in added.model_dump_json() and 'sha256' not in added.model_dump_json()
    removed=v.run('remove_photo',VersionInput(expected_lock_version=2),photo_id=row.id)
    assert removed.photos==[] and row.removed_at==hour(0) and row.removed_by==v.actor.member_id
    assert v.resource.lock_version==3 and len(v.state.photos)==1
    with pytest.raises(AuthError) as caught: asyncio.run(v.repo.content(v.person.user_id,v.person.crew_id,v.resource.id,row.id))
    assert caught.value.code=='NOT_FOUND'


def test_final_attach_rechecks_twenty_photo_limit(monkeypatch):
    v=fixture(monkeypatch)
    identity=PhotoUploadIdentity(expected_lock_version=1,description=None,source_sha256='3'*64)
    assert asyncio.run(v.repo.prepare_upload(v.person.user_id,v.person.crew_id,v.resource.id,identity,'key')) is None
    v.state.photos=[photo(v) for _ in range(20)]
    with pytest.raises(AuthError) as caught: v.run('upload_photo',identity,new_photo=photo(v))
    assert caught.value.code=='LOCATION_PHOTO_LIMIT' and v.resource.lock_version==1
    v.session.add.assert_not_called()


def test_photo_description_and_completed_request_replay_do_not_duplicate_upload(monkeypatch):
    v=fixture(monkeypatch);row=photo(v);v.state.photos=[row]
    body=PhotoDescriptionEdit(expected_lock_version=1,description='新说明')
    first=v.run('edit_photo',body,photo_id=row.id)
    v.state.receipt=next(call.args[0] for call in v.session.add.call_args_list if isinstance(call.args[0],ResourceReceipt))
    assert v.run('edit_photo',body,photo_id=row.id)==first and v.resource.lock_version==2
    with pytest.raises(AuthError) as caught: v.run('edit_photo',body.model_copy(update={'description':'另一份内容'}),photo_id=row.id)
    assert caught.value.code=='IDEMPOTENCY_KEY_REUSED'
    v.state.receipt.membership_joined_at=hour(1)
    with pytest.raises(AuthError) as caught: v.run('edit_photo',body,photo_id=row.id)
    assert caught.value.code=='MEMBERSHIP_CHANGED'


def test_wrong_resource_type_or_cross_crew_uses_hidden_scope(monkeypatch):
    v=fixture(monkeypatch)
    v.session.scalar.side_effect=None;v.session.scalar.return_value=None
    with pytest.raises(AuthError) as caught: asyncio.run(v.repo.get(v.person.user_id,v.person.crew_id,v.resource.id))
    assert caught.value.code=='NOT_FOUND'
    query=v.session.scalar.call_args.args[0];parameters=query.compile().params
    assert {v.person.crew_id,v.resource.id,'location'}<=set(parameters.values())


def test_failed_commit_cannot_return_success(monkeypatch):
    v=fixture(monkeypatch);v.tx.__aexit__.side_effect=OperationalError('private SQL',{},Exception('lost commit'))
    with pytest.raises(OperationalError): v.run('edit_details',LocationDetailsEdit(expected_lock_version=1,address='教学楼'))


def test_service_only_persists_normalized_files_after_authorized_precheck(monkeypatch,tmp_path):
    v=fixture(monkeypatch)
    processed=images.normalize_photo(image_bytes())
    decoder=Mock(return_value=processed);monkeypatch.setattr(service_module,'normalize_in_process',decoder)
    storage=PrivateFileStorage(tmp_path);service=LocationService(v.repo,Mock(record_failure=AsyncMock()),storage)
    user=NS(id=v.person.user_id,status='active',profile_completed=True)
    result=asyncio.run(service.execute(user,'upload_photo','a'*32,crew_id=v.person.crew_id,resource_id=v.resource.id,
        upload=UploadFile(BytesIO(image_bytes()),filename='untrusted.svg'),expected_lock_version=1,description='入口',idempotency_key='key'))
    assert len(result.photos)==1 and len(list(tmp_path.rglob('*.*')))==2
    assert v.state.photos[0].sha256==sha256(processed.content).hexdigest()
    # Filename and claimed MIME do not choose the accepted image format.
    assert v.state.photos[0].file_format=='jpeg'


def test_unauthorized_upload_does_not_start_decoder_or_write_file(monkeypatch):
    repo=Mock(prepare_upload=AsyncMock(side_effect=AuthError(403,'FORBIDDEN','Denied')))
    decoder=Mock();monkeypatch.setattr(service_module,'normalize_in_process',decoder)
    storage=Mock();service=LocationService(repo,Mock(record_failure=AsyncMock()),storage)
    with pytest.raises(AuthError): asyncio.run(service.execute(NS(id=uuid4(),status='active',profile_completed=True),'upload_photo','a'*32,
        crew_id=uuid4(),resource_id=uuid4(),upload=UploadFile(BytesIO(image_bytes())),expected_lock_version=1,description=None,idempotency_key='key'))
    decoder.assert_not_called();storage.put.assert_not_called()


def test_upload_rechecks_authorization_after_processing(monkeypatch,tmp_path):
    v=fixture(monkeypatch)
    denied=replace(v.actor,roles=frozenset({'director'}),permissions=frozenset({'resource.read'}))
    monkeypatch.setattr(module,'resource_actor',AsyncMock(side_effect=[v.actor,denied]))
    monkeypatch.setattr(service_module,'normalize_in_process',Mock(return_value=images.normalize_photo(image_bytes())))
    service=LocationService(v.repo,Mock(record_failure=AsyncMock()),PrivateFileStorage(tmp_path))
    with pytest.raises(AuthError) as caught:
        asyncio.run(service.execute(NS(id=v.person.user_id,status='active',profile_completed=True),'upload_photo','a'*32,
            crew_id=v.person.crew_id,resource_id=v.resource.id,upload=UploadFile(BytesIO(image_bytes())),expected_lock_version=1,description=None,idempotency_key='key'))
    assert caught.value.code=='FORBIDDEN' and v.resource.lock_version==1 and not v.state.photos
    v.session.add.assert_not_called()


def test_photo_input_limits_and_description_null_semantics():
    assert PhotoDescriptionEdit(expected_lock_version=1,description='  ').description is None
    with pytest.raises(ValidationError): PhotoDescriptionEdit(expected_lock_version=1,description='字'*501)
    with pytest.raises(ValidationError): LocationDetailsEdit(expected_lock_version=1)


def test_multipart_csrf_and_private_preview_full_streams(crew_client,tmp_path):
    client,audits,user,crew=crew_client
    resource_id,photo_id=uuid4(),uuid4()
    view=NS(resource_id=resource_id,resource_name='场地',resource_status='available',resource_lock_version=1,
        details={'address':None,'access_instructions':None,'shooting_constraints':None},photos=[],allowed_actions=[])
    repo=Mock(prepare_upload=AsyncMock(return_value=view),content=AsyncMock())
    storage=PrivateFileStorage(tmp_path);client.app.state.locations=LocationService(repo,audits,storage)
    path=f'/api/v1/crews/{crew.id}/locations/{resource_id}/photos'
    headers={'Idempotency-Key':str(uuid4())}
    assert client.post(path,files={'file':('photo.jpg',image_bytes())},data={'expected_lock_version':'1'},headers=headers).status_code==201
    assert client.post(path,files={'file':('photo.jpg',image_bytes())},data={'expected_lock_version':'1'},headers=headers|{'X-CSRF-Token':'bad'}).status_code==403
    assert client.post(path,json={},headers=headers).status_code==415
    full=image_bytes();preview=images.normalize_photo(full).preview
    storage.put('full.jpeg',full);storage.put('preview.webp',preview)
    repo.content.return_value=NS(id=photo_id,storage_key='full.jpeg',preview_storage_key='preview.webp',file_format='jpeg')
    url=path+f'/{photo_id}/content'
    response=client.get(url)
    assert response.content==preview and response.headers['content-type']=='image/webp'
    assert response.headers['cache-control']=='no-store' and response.headers['cross-origin-resource-policy']=='same-origin'
    assert client.get(url+'?variant=full').content==full
    repo.content.side_effect=AuthError(404,'NOT_FOUND','Hidden')
    assert client.get(url).status_code==404


def test_location_migration_uses_same_crew_foreign_keys_and_active_index(monkeypatch):
    monkeypatch.setattr(config_module,'load_settings',lambda:Settings(_env_file=None,database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    output=StringIO();command.upgrade(Config('alembic.ini',output_buffer=output),'head',sql=True)
    sql=output.getvalue()
    assert '0020_locations' in sql and 'CREATE TABLE location_photos' in sql
    assert 'FOREIGN KEY(crew_id, location_resource_id) REFERENCES resources (crew_id, id)' in sql
    assert 'CREATE INDEX ix_location_photos_active' in sql and 'WHERE removed_at IS NULL' in sql
    assert 'width_px::bigint * height_px <= 25000000' in sql
