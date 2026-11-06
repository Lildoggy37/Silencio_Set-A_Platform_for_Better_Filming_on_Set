"""Scene requirements are versioned content, not reservations or stock movements."""
from uuid import UUID, uuid4

from sqlalchemy import select

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.repository import not_found
from silencio_set.creative.models import SceneCharacter, SceneResourceNeed, SceneRevision
from silencio_set.resources.models import Resource
from silencio_set.resources.repository import require


async def scene_links(session, crew_id, revisions):
    result = {row.id: {'characters': [], 'resource_needs': []} for row in revisions}
    if not result:
        return result
    for model, key, fields in (
        (SceneCharacter, 'characters', ('name',)),
        (SceneResourceNeed, 'resource_needs', ('resource_type', 'name', 'quantity', 'unit', 'resource_id')),
    ):
        rows = (await session.scalars(select(model).where(
            model.crew_id == crew_id, model.scene_revision_id.in_(result),
        ).order_by(model.name, model.id))).all()
        for row in rows:
            result[row.scene_revision_id][key].append({
                field: str(value) if isinstance(value, UUID) else value
                for field in fields for value in (getattr(row, field),)
            })
    return result


async def validate_links(session, crew_id, actor, needs):
    ids = {need.resource_id for need in needs if need.resource_id}
    if not ids:
        return
    require(actor, 'resource.read')
    rows = (await session.scalars(select(Resource).where(
        Resource.crew_id == crew_id, Resource.id.in_(ids),
    ))).all()
    resources = {row.id: row for row in rows}
    if resources.keys() != ids:
        raise not_found()
    for need in needs:
        if need.resource_id is None:
            continue
        resource = resources[need.resource_id]
        if resource.resource_type != need.resource_type or resource.unit != need.unit:
            raise AuthError(409, 'RESOURCE_NEED_MISMATCH', '关联资源的类型或单位与需求不一致')
        if resource.status != 'available':
            raise AuthError(409, 'RESOURCE_DISABLED', '关联资源已停用，请解除关联或选择其他资源')


async def validate_breakdown_links(session, crew_id, actor, breakdown_id):
    needs = (await session.scalars(select(SceneResourceNeed).join(
        SceneRevision, SceneRevision.id == SceneResourceNeed.scene_revision_id,
    ).where(SceneResourceNeed.crew_id == crew_id, SceneRevision.breakdown_id == breakdown_id))).all()
    await validate_links(session, crew_id, actor, needs)


def add_links(session, crew_id, revision_id, content):
    session.add_all([
        SceneCharacter(id=uuid4(), crew_id=crew_id, scene_revision_id=revision_id, **item)
        for item in content['characters']
    ])
    session.add_all([
        SceneResourceNeed(id=uuid4(), crew_id=crew_id, scene_revision_id=revision_id, **{
            **item, 'resource_id': UUID(item['resource_id']) if item['resource_id'] else None,
        })
        for item in content['resource_needs']
    ])
