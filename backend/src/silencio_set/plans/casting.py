"""Casting checks and immutable evidence for plan publication."""
from sqlalchemy import select

from silencio_set.accounts.models import User
from silencio_set.creative.models import SceneCharacter, CharacterCasting
from silencio_set.crews.models import CrewMember, MemberRole
from silencio_set.plans.schemas import CheckIssue


async def collect_casting(session, crew_id, scenes, script_versions):
    characters = (await session.scalars(select(SceneCharacter).where(SceneCharacter.crew_id == crew_id,
        SceneCharacter.scene_revision_id.in_(scenes)).order_by(SceneCharacter.scene_revision_id, SceneCharacter.name))).all() if scenes else []
    if not characters:
        return []
    script_ids = {script_versions[scenes[row.scene_revision_id].script_version_id].script_id for row in characters}
    bindings = (await session.scalars(select(CharacterCasting).where(CharacterCasting.crew_id == crew_id,
        CharacterCasting.script_id.in_(script_ids), CharacterCasting.character_name.in_({row.name for row in characters})))).all()
    bound = {(row.script_id, row.character_name): row for row in bindings}
    ids = {row.member_id for row in bindings}
    members = (await session.scalars(select(CrewMember).where(CrewMember.crew_id == crew_id, CrewMember.id.in_(ids)))).all() if ids else []
    people = {row.id: row for row in members}
    roles = set((await session.scalars(select(MemberRole.member_id).where(MemberRole.crew_id == crew_id,
        MemberRole.member_id.in_(ids), MemberRole.role_code == 'actor'))).all()) if ids else set()
    active_users = set((await session.scalars(select(User.id).where(User.id.in_({row.user_id for row in members}), User.status == 'active'))).all()) if members else set()
    result = []
    for character in characters:
        script_id = script_versions[scenes[character.scene_revision_id].script_version_id].script_id
        row = bound.get((script_id, character.name))
        member = people.get(row.member_id) if row else None
        valid = member is not None and member.status == 'active' and member.joined_at == row.membership_joined_at and member.id in roles and member.user_id in active_users
        result.append({'scene_revision_id': character.scene_revision_id, 'character_name': character.name,
            'binding_updated_at': row.updated_at if row else None,
            'member_id': row.member_id if row else None, 'membership_joined_at': row.membership_joined_at if row else None,
            'display_name': member.display_name if valid else row.display_name if row else None,
            'status': 'active' if valid else 'invalid' if row else 'unassigned'})
    return result


def extend_casting(report, items, castings):
    for item in items:
        cast = [row for row in castings if row['scene_revision_id'] == item['scene_revision_id']]
        item['characters'] = cast
        participants = {(link['member_id'], link['membership_joined_at']) for link in item['members']}
        for row in cast:
            if row['status'] == 'unassigned':
                code, message = 'CHARACTER_UNCAST', f'人物“{row["character_name"]}”尚未绑定演员'
            elif row['status'] == 'invalid':
                code, message = 'CAST_MEMBER_INVALID', f'人物“{row["character_name"]}”的演员身份已失效，请重新绑定'
            elif (row['member_id'], row['membership_joined_at']) not in participants:
                code, message = 'CAST_MEMBER_UNSCHEDULED', f'人物“{row["character_name"]}”的演员尚未加入本场参与人员'
            else:
                continue
            report.blockers.append(CheckIssue(code=code, message=message, session_id=item['id'], member_id=row['member_id']))
