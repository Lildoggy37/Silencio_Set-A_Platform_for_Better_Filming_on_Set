"""Opt-in real PostgreSQL coverage of the reduced approval paths."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from silencio_set.accounts.models import User
from silencio_set.accounts.passwords import PasswordService
from test_postgres_flow import PASSWORD, request, csrf, login, saved_session, restore_session, seed_actor


async def seed(sessions):
    now = datetime.now(UTC)
    suffix = uuid4().hex
    password = PasswordService().hash(PASSWORD)
    emails = [f'workflow-{role}-{suffix}@example.com' for role in ('director', 'member')]
    async with sessions() as session, session.begin():
        users = [User(email=email, email_normalized=email, password_hash=password,
                      email_verified_at=now, display_name=role, profile_completed_at=now)
                 for email, role in zip(emails, ('导演', '成员'))]
        session.add_all(users)
        await session.flush()
        return emails, users[1].id


def test_reduced_workflows_with_permissions_capacity_and_replay(postgres_client):
    client = postgres_client
    emails, member_user_id = client.portal.call(seed, client.app.state.auth.repository.sessions)
    login(client, emails[0])
    crew = request(client, 'POST', '/crews', {'name': '减负联调剧组', 'project_name': '准备开拍'}, status=201)
    root = f"/crews/{crew['id']}"
    own = crew['membership']
    manager = request(client, 'PUT', root + f"/members/{own['id']}/roles", {
        'expected_lock_version': own['lock_version'], 'role_codes': ['admin', 'director', 'producer'],
    })
    member_id = client.portal.call(seed_actor, client.app.state.auth.repository.sessions, crew['id'], member_user_id)
    director_session = saved_session(client)
    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=2, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=2)

    task = request(client, 'POST', root + '/tasks', {
        'title': '核对设备清单', 'owner_member_id': manager['id'], 'requires_review': False, 'due_at': end.isoformat(),
    }, status=201)
    assert task['requires_review'] is False and 'submit' in task['allowed_actions']
    task_path = root + f"/tasks/{task['id']}"
    login(client, emails[1])
    denied = client.post('/api/v1' + task_path + '/submissions',
        json={'expected_lock_version': 1, 'completion_note': '代为完成'}, headers={'Idempotency-Key': str(uuid4())})
    assert denied.status_code == 404
    restore_session(client, director_session)
    completion = {'expected_lock_version': task['lock_version'], 'completion_note': '清单已核对'}
    key = str(uuid4())
    done = request(client, 'POST', task_path + '/submissions', completion, key=key)
    assert done['status'] == 'completed'
    assert request(client, 'POST', task_path + '/submissions', completion, key=key) == done
    detail = request(client, 'GET', task_path)
    assert detail['submission_count'] == 1
    assert detail['current_submission']['review_status'] == 'completed'
    assert detail['current_submission']['reviewed_by'] is None

    # A legacy caller providing a reviewer keeps the acceptance workflow.
    legacy = request(client, 'POST', root + '/tasks', {
        'title': '重要交付', 'owner_member_id': manager['id'], 'reviewer_member_id': manager['id'], 'due_at': end.isoformat(),
    }, status=201)
    assert legacy['requires_review'] is True
    legacy_path = root + f"/tasks/{legacy['id']}"
    active = request(client, 'POST', legacy_path + '/start', {'expected_lock_version': legacy['lock_version']})
    pending = request(client, 'POST', legacy_path + '/submissions', {
        'expected_lock_version': active['lock_version'], 'completion_note': '提交重要成果'})
    assert pending['status'] == 'pending_review'
    submitted = request(client, 'GET', legacy_path)['current_submission']
    accepted = request(client, 'POST', legacy_path + '/reviews', {
        'expected_lock_version': pending['lock_version'], 'submission_id': submitted['id'], 'decision': 'accept'})
    assert accepted['status'] == 'completed'

    equipment = request(client, 'POST', root + '/resources', {
        'name': '普通灯架', 'resource_type': 'equipment', 'unit': '套', 'total_quantity': 1,
        'manager_member_id': manager['id'], 'approval_policy': 'automatic',
    }, status=201)
    login(client, emails[1])
    booking_body = {'resource_id': equipment['id'], 'starts_at': start.isoformat(), 'ends_at': end.isoformat(), 'quantity': 1}
    key = str(uuid4())
    booking = request(client, 'POST', root + '/resource-reservations', booking_body, status=201, key=key)
    assert booking['status'] == 'confirmed' and booking['confirmation_mode'] == 'automatic'
    assert booking['reviewed_by'] is None
    replay = request(client, 'POST', root + '/resource-reservations', booking_body, status=201, key=key)
    assert replay['id'] == booking['id'] and replay['status'] == 'confirmed'
    assert request(client, 'GET', root + f"/resource-reservations?resource_id={equipment['id']}")['total'] == 1
    conflict = client.post('/api/v1' + root + '/resource-reservations', json=booking_body,
                           headers={'Idempotency-Key': str(uuid4())})
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'RESOURCE_CAPACITY_CONFLICT'
    denied = client.patch('/api/v1' + root + f"/resources/{equipment['id']}",
                          json={'expected_lock_version': 1, 'approval_policy': 'manual'})
    assert denied.status_code == 403
    restore_session(client, director_session)
    request(client, 'PATCH', root + f"/resources/{equipment['id']}", {
        'expected_lock_version': 1, 'approval_policy': 'manual'})
    login(client, emails[1])
    pending = request(client, 'POST', root + '/resource-reservations', {
        **booking_body, 'starts_at': end.isoformat(), 'ends_at': (end + timedelta(hours=1)).isoformat(),
    }, status=201)
    assert pending['status'] == 'pending'
    restore_session(client, director_session)

    script = request(client, 'POST', root + '/scripts', {'title': '准备开拍'}, status=201)['script']
    script_path = root + f"/scripts/{script['id']}"
    added = request(client, 'POST', script_path + '/versions', {
        'expected_lock_version': script['lock_version'], 'text': '走廊，日。两人走进画面。'}, status=201)
    version = added['version']
    request(client, 'POST', script_path + f"/versions/{version['id']}/confirm", {'expected_lock_version': version['lock_version']})
    request(client, 'PUT', script_path + '/working-version', {
        'expected_lock_version': added['script']['lock_version'], 'version_id': version['id']})
    draft = request(client, 'POST', root + '/breakdowns', {'source_id': version['id']}, status=201)
    creative_path = root + f"/breakdowns/{draft['id']}"
    draft = request(client, 'PUT', creative_path, {'expected_lock_version': draft['lock_version'],
        'scenes': [{'scene_number': '01', 'location_text': '走廊', 'time_of_day': 'day', 'source_start': 1, 'source_end': 1}]})
    confirmed = request(client, 'POST', creative_path + '/confirm', {'expected_lock_version': draft['lock_version']})
    assert confirmed['review_status'] == 'confirmed'
    assert len(request(client, 'GET', creative_path + '/reviews')['items']) == 1
    request(client, 'PUT', root + f"/members/{member_id}/roles", {
        'expected_lock_version': 1, 'role_codes': ['actor', 'writer']})
    login(client, emails[1])
    other = request(client, 'POST', root + '/breakdowns', {'source_id': version['id']}, status=201)
    other_path = root + f"/breakdowns/{other['id']}"
    other = request(client, 'PUT', other_path, {'expected_lock_version': other['lock_version'],
        'scenes': [{'scene_number': '02', 'location_text': '走廊', 'time_of_day': 'day', 'source_start': 1, 'source_end': 1}]})
    writer_session = saved_session(client)
    restore_session(client, director_session)
    denied = client.post('/api/v1' + other_path + '/confirm', json={'expected_lock_version': other['lock_version']},
                         headers={'Idempotency-Key': str(uuid4())})
    assert denied.status_code == 403
    restore_session(client, writer_session)
    other = request(client, 'POST', other_path + '/submit', {'expected_lock_version': other['lock_version']})
    restore_session(client, director_session)
    other = request(client, 'POST', other_path + '/reviews', {
        'expected_lock_version': other['lock_version'], 'decision': 'accept'})
    assert other['review_status'] == 'confirmed'

    location = request(client, 'POST', root + '/resources', {
        'name': '走廊', 'resource_type': 'location', 'unit': '处', 'total_quantity': 1,
        'manager_member_id': manager['id'], 'approval_policy': 'automatic',
    }, status=201)
    place_booking = request(client, 'POST', root + '/resource-reservations', {
        **booking_body, 'resource_id': location['id']}, status=201)
    plan = request(client, 'POST', root + '/shooting-plans', {'name': '正式拍摄日'}, status=201)
    plan_version = request(client, 'POST', root + f"/shooting-plans/{plan['id']}/versions", {}, status=201)
    plan_path = root + f"/plan-versions/{plan_version['id']}"
    calendar_response = client.get('/api/v1' + root + '/members/me/availability',
        params={'range_start': start.isoformat(), 'range_end': end.isoformat()})
    assert calendar_response.status_code == 200
    joined_at = calendar_response.json()['data']['membership_joined_at']
    plan_version = request(client, 'PUT', plan_path + '/sessions', {
        'expected_lock_version': plan_version['lock_version'], 'sessions': [{
            'scene_revision_id': confirmed['items'][0]['id'], 'starts_at': start.isoformat(), 'ends_at': end.isoformat(),
            'location_resource_id': location['id'], 'location_reservation_id': place_booking['id'],
            'members': [{'member_id': manager['id'], 'membership_joined_at': joined_at}],
        }]})
    proof = request(client, 'POST', plan_path + '/preflight', {'expected_lock_version': plan_version['lock_version']})
    assert not proof['blockers']
    request(client, 'POST', plan_path + '/publish', {
        'expected_lock_version': proof['checked_lock_version'], 'checked_data_revision': proof['checked_data_revision'],
        'expected_current_published_version_id': proof['expected_current_published_version_id'],
        'confirmed_unknown_item_ids': [item['item_id'] for item in proof['unknown_items']],
    })
    sheet = request(client, 'POST', root + '/call-sheets', {
        'source_plan_version_id': plan_version['id'], 'shooting_date': start.date().isoformat()}, status=201)
    sheet_path = root + f"/call-sheet-versions/{sheet['id']}"
    sheet = request(client, 'PATCH', sheet_path, {
        'expected_lock_version': sheet['lock_version'], 'gathering_at': (start - timedelta(minutes=30)).isoformat(),
        'gathering_location': '走廊入口'})
    body = {'expected_lock_version': sheet['lock_version'], 'expected_current_published_version_id': None}
    login(client, emails[1])
    denied = client.post('/api/v1' + sheet_path + '/publish', json=body, headers={'Idempotency-Key': str(uuid4())})
    assert denied.status_code == 403
    restore_session(client, director_session)
    key = str(uuid4())
    published = request(client, 'POST', sheet_path + '/publish', body, key=key)
    assert published['status'] == 'published' and published['validity_status'] == 'valid'
    assert request(client, 'POST', sheet_path + '/publish', body, key=key) == published
    assert len(request(client, 'GET', sheet_path + '/reviews')['items']) == 1
    legacy_sheet = request(client, 'POST', root + f"/call-sheets/{published['call_sheet_id']}/versions", {
        'source_plan_version_id': plan_version['id'], 'copy_from': published['id']}, status=201)
    legacy_sheet_path = root + f"/call-sheet-versions/{legacy_sheet['id']}"
    for suffix, fields in [('/submit', {}), ('/reviews', {'decision': 'accept'})]:
        legacy_sheet = request(client, 'POST', legacy_sheet_path + suffix, {
            'expected_lock_version': legacy_sheet['lock_version'], **fields})
    legacy_sheet = request(client, 'POST', legacy_sheet_path + '/publish', {
        'expected_lock_version': legacy_sheet['lock_version'], 'expected_current_published_version_id': published['id']})
    assert legacy_sheet['status'] == 'published'
