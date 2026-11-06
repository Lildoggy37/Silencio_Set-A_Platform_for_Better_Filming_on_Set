from dataclasses import replace
from io import StringIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from silencio_set.accounts.errors import AuthError
from silencio_set.core import config as config_module
from silencio_set.core.config import Settings
from silencio_set.creative import repository as module
from silencio_set.creative.models import SceneCharacter, SceneResourceNeed, SceneRevision, CreativeReview
from silencio_set.creative.schemas import SceneInput, SceneSave, Source, Decision
from silencio_set.resources.models import Resource
from test_creative import fixture, run


def scene(**changes):
    return SceneInput(**dict(scene_number='01', location_text='走廊', source_start=1, source_end=2, **changes))


def setup(monkeypatch, *, linked=False):
    repo, session, tx, actor, row, source, entry = fixture(monkeypatch)
    actor = replace(actor, permissions=actor.permissions | {'resource.read'})
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=actor))
    resource = Resource(id=uuid4(), crew_id=row.crew_id, resource_type='equipment', name='摄影机', unit='台', status='available')
    character = SceneCharacter(id=uuid4(), crew_id=row.crew_id, scene_revision_id=entry.id, name='林雨')
    need = SceneResourceNeed(id=uuid4(), crew_id=row.crew_id, scene_revision_id=entry.id,
                             resource_type='equipment', name='摄影机', quantity=2, unit='台', resource_id=resource.id if linked else None)
    original = session.scalars.side_effect

    async def scalars(query):
        entity = query.column_descriptions[0].get('entity')
        if entity in {Resource, SceneCharacter, SceneResourceNeed}:
            return Mock(all=Mock(return_value={Resource: [resource], SceneCharacter: [character], SceneResourceNeed: [need]}[entity]))
        return await original(query)

    session.scalars.side_effect = scalars
    return repo, session, actor, row, entry, resource, character, need


@pytest.mark.parametrize('changes', [
    {'characters': [{'name': '林雨'}, {'name': ' 林雨 '}]},
    {'resource_needs': [{'resource_type': 'equipment', 'name': '摄影机', 'quantity': 0, 'unit': '台'}]},
    {'resource_needs': [{'resource_type': 'equipment', 'name': '摄影机', 'quantity': 1, 'unit': '台'}] * 2},
    {'characters': [{'name': ' '}]},
])
def test_names_and_need_quantities_are_validated(changes):
    with pytest.raises(ValidationError):
        scene(**changes)


def test_same_resource_cannot_be_listed_twice_under_different_names():
    resource_id = uuid4()
    with pytest.raises(ValidationError):
        scene(resource_needs=[dict(resource_type='equipment', name=name, quantity=1, unit='台', resource_id=resource_id)
                              for name in ('主机', '备机')])


def test_save_writes_normalized_links_without_stock_or_booking_changes(monkeypatch):
    repo, session, _, row, entry, resource, _, _ = setup(monkeypatch)
    result = run(repo, row, 'save', SceneSave(expected_lock_version=1, scenes=[scene(
        scene_id=entry.scene_id, characters=[{'name': '林雨'}],
        resource_needs=[dict(resource_type='equipment', name='主摄影机', quantity=2, unit='台', resource_id=resource.id)],
    )]))
    links = [item for call in session.add_all.call_args_list for item in call.args[0]]
    assert any(isinstance(item, SceneCharacter) and item.name == '林雨' for item in links)
    assert any(isinstance(item, SceneResourceNeed) and item.resource_id == resource.id and item.quantity == 2 for item in links)
    revision = next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], SceneRevision))
    assert 'resource_needs' not in revision.content and 'characters' not in revision.content
    assert result.lock_version == 2


def test_unbound_needs_can_be_saved_without_resource_directory_permission(monkeypatch):
    repo, session, _, _, row, _, entry = fixture(monkeypatch)
    result = run(repo, row, 'save', SceneSave(expected_lock_version=1, scenes=[scene(
        scene_id=entry.scene_id, resource_needs=[dict(resource_type='consumable', name='电池', quantity=4, unit='节')],
    )]))
    assert result.lock_version == 2
    linked = [item for call in session.add_all.call_args_list for item in call.args[0] if isinstance(item, SceneResourceNeed)]
    assert len(linked) == 1 and linked[0].resource_id is None


@pytest.mark.parametrize('change,code', [
    ({'unit': '套'}, 'RESOURCE_NEED_MISMATCH'),
    ({'resource_type': 'prop'}, 'RESOURCE_NEED_MISMATCH'),
    ({'status': 'disabled'}, 'RESOURCE_DISABLED'),
])
def test_invalid_links_fail_before_replacing_the_draft(monkeypatch, change, code):
    repo, session, _, row, entry, resource, _, _ = setup(monkeypatch)
    for key, value in change.items(): setattr(resource, key, value)
    with pytest.raises(AuthError) as caught:
        run(repo, row, 'save', SceneSave(expected_lock_version=1, scenes=[scene(
            scene_id=entry.scene_id, resource_needs=[dict(resource_type='equipment', name='摄影机', quantity=1, unit='台', resource_id=resource.id)],
        )]))
    assert caught.value.code == code
    session.execute.assert_not_awaited()


def test_unavailable_cross_crew_or_unknown_resource_is_hidden(monkeypatch):
    repo, session, _, row, entry, _, _, _ = setup(monkeypatch)
    with pytest.raises(AuthError) as caught:
        run(repo, row, 'save', SceneSave(expected_lock_version=1, scenes=[scene(
            scene_id=entry.scene_id, resource_needs=[dict(resource_type='equipment', name='摄影机', quantity=1, unit='台', resource_id=uuid4())],
        )]))
    assert caught.value.status == 404
    session.execute.assert_not_awaited()


def test_linking_requires_resource_read_permission_even_with_scene_edit(monkeypatch):
    repo, session, actor, row, entry, resource, _, _ = setup(monkeypatch)
    monkeypatch.setattr(module, 'resource_actor', AsyncMock(return_value=replace(actor, permissions=actor.permissions - {'resource.read'})))
    with pytest.raises(AuthError) as caught:
        run(repo, row, 'save', SceneSave(expected_lock_version=1, scenes=[scene(
            scene_id=entry.scene_id, resource_needs=[dict(resource_type='equipment', name='摄影机', quantity=1, unit='台', resource_id=resource.id)],
        )]))
    assert caught.value.code == 'FORBIDDEN'
    session.execute.assert_not_awaited()


def test_copy_keeps_requirements_and_creates_new_child_records(monkeypatch):
    repo, session, _, row, _, _, character, need = setup(monkeypatch, linked=True)
    run(repo, row, 'create', Source(source_id=row.script_version_id, copy_from=row.id))
    copied = [item for call in session.add_all.call_args_list for item in call.args[0]]
    new_character = next(item for item in copied if isinstance(item, SceneCharacter))
    new_need = next(item for item in copied if isinstance(item, SceneResourceNeed))
    assert new_character.id != character.id and new_character.scene_revision_id != character.scene_revision_id
    assert new_need.id != need.id and new_need.resource_id == need.resource_id


def test_review_rechecks_resource_state_and_rejection_still_allows_repair(monkeypatch):
    repo, session, _, row, _, resource, _, _ = setup(monkeypatch, linked=True)
    row.review_status = 'pending_review'; resource.status = 'disabled'
    with pytest.raises(AuthError) as caught:
        run(repo, row, 'review', Decision(expected_lock_version=1, decision='accept'))
    assert caught.value.code == 'RESOURCE_DISABLED'
    assert not any(isinstance(call.args[0], CreativeReview) for call in session.add.call_args_list)
    result = run(repo, row, 'review', Decision(expected_lock_version=1, decision='reject', review_note='更换停用设备'))
    review = next(call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], CreativeReview))
    assert result.review_status == 'rejected'
    assert review.content_snapshot['items'][0]['resource_needs'][0]['resource_id'] == str(resource.id)


def test_migration_keeps_links_in_the_same_crew_and_cascades_only_revision_children(monkeypatch):
    monkeypatch.setattr(config_module, 'load_settings', lambda: Settings(_env_file=None, database_url='postgresql+asyncpg://user:example@localhost:5432/test'))
    output = StringIO()
    command.upgrade(Config('alembic.ini', output_buffer=output), 'head', sql=True)
    sql = output.getvalue()
    assert '0014_scene_needs' in sql and 'CREATE TABLE scene_characters' in sql
    assert 'FOREIGN KEY(crew_id, resource_id) REFERENCES resources (crew_id, id) ON DELETE RESTRICT' in sql
    assert 'UNIQUE (scene_revision_id, resource_id)' in sql
