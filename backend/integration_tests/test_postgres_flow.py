"""Real HTTP/business repositories, migrations, FK/JSONB/transactions and Argon2.

Users and actor membership are fixtures, not a claim of SMTP/invitation coverage.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from httpx import Cookies
from sqlalchemy import select

from silencio_set.accounts.models import User
from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.passwords import PasswordService
from silencio_set.crews.models import CrewMember, MemberRole
from silencio_set.notifications.repository import NotificationConsumer

PASSWORD = 'postgres integration sample password'


def request(client, method, path, body=None, status=200, key=None):
    options = {}
    if body is not None:
        options['json'] = body
    if method != 'GET':
        options['headers'] = {'Idempotency-Key': key or str(uuid4())}
    response = client.request(method, '/api/v1' + path, **options)
    assert response.status_code == status, f'{method} {path}: {response.status_code} {response.text}'
    return response.json()['data']


def csrf(client):
    client.headers['X-CSRF-Token'] = request(client, 'GET', '/auth/csrf')['csrf_token']


def login(client, email):
    client.cookies.clear()
    csrf(client)
    request(client, 'POST', '/auth/login', {'email': email, 'password': PASSWORD})
    csrf(client)


def saved_session(client):
    return Cookies(client.cookies), client.headers['X-CSRF-Token']


def restore_session(client, saved):
    client.cookies.clear()
    client.cookies.update(saved[0])
    client.headers['X-CSRF-Token'] = saved[1]


async def seed_users(sessions):
    now = datetime.now(UTC)
    hashed = PasswordService().hash(PASSWORD)
    async with sessions() as session, session.begin():
        director = User(email='director@example.com', email_normalized='director@example.com',
            password_hash=hashed, email_verified_at=now)
        actor = User(email='actor@example.com', email_normalized='actor@example.com',
            password_hash=hashed, email_verified_at=now, display_name='演员同学', profile_completed_at=now)
        session.add_all([director, actor])
        await session.flush()
        return actor.id


async def seed_actor(sessions, crew_id, actor_id):
    async with sessions() as session, session.begin():
        member = CrewMember(crew_id=UUID(crew_id), user_id=actor_id, display_name='演员同学')
        session.add(member)
        await session.flush()
        session.add(MemberRole(crew_id=member.crew_id, member_id=member.id, role_code='actor'))
        return str(member.id)


async def consume_notices(sessions):
    consumer = NotificationConsumer(sessions)
    count = 0
    while await consumer.consume_once():
        count += 1
        assert count <= 10, 'Unexpected event backlog in an isolated database'
    return count


async def check_cache_semantics(cache, old_token, current_token):
    assert await cache.read_session(old_token) is None
    current = await cache.read_session(current_token)
    assert current.user_id is not None
    ttl = await cache.redis.ttl(cache.session_key(current_token))
    assert 0 < ttl <= cache.settings.auth_session_ttl
    purpose = 'integration-' + uuid4().hex

    async def attempt():
        try:
            await cache.limit(purpose, 'integration-ip', 'integration-user', ip_limit=3, identity_limit=3)
            return 'accepted'
        except AuthError as error:
            assert error.status == 429 and error.retry_after > 0
            return 'limited'

    outcomes = await asyncio.gather(*(attempt() for _ in range(8)))
    assert outcomes.count('accepted') == 3
    assert outcomes.count('limited') == 5


def test_preproduction_publication_and_source_change(postgres_client):
    client = postgres_client
    sessions = client.app.state.auth.repository.sessions
    actor_user_id = client.portal.call(seed_users, sessions)
    csrf(client)
    old_token = client.cookies.get('silencio_session')
    request(client, 'POST', '/auth/login', {'email':'director@example.com', 'password':PASSWORD})
    csrf(client)
    client.portal.call(check_cache_semantics, client.app.state.auth.cache,
        old_token, client.cookies.get('silencio_session'))
    request(client, 'PATCH', '/users/me/profile', {'display_name':'导演同学', 'expected_lock_version':1})
    crew = request(client, 'POST', '/crews', {'name':'寂静剧组', 'project_name':'雨停之前'}, status=201)
    root = f"/crews/{crew['id']}"
    membership = crew['membership']
    manager = request(client, 'PUT', root + f"/members/{membership['id']}/roles", {
        'expected_lock_version':membership['lock_version'], 'role_codes':['admin','director','producer'],
    })
    actor_id = client.portal.call(seed_actor, sessions, crew['id'], actor_user_id)
    director_session = saved_session(client)

    start = (datetime.now(UTC) + timedelta(days=2)).replace(hour=2, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=2)
    window = {'range_start':start.isoformat(), 'range_end':end.isoformat()}
    login(client, 'actor@example.com')
    availability_path = root + '/members/me/availability'
    own = client.get('/api/v1' + availability_path, params=window).json()['data']
    request(client, 'PUT', availability_path, {**window, 'expected_lock_version':own['lock_version'],
        'expected_membership_joined_at':own['membership_joined_at'], 'intervals':[
            {'starts_at':start.isoformat(),'ends_at':end.isoformat(),'availability':'available'},
        ]})
    actor_session = saved_session(client)
    restore_session(client, director_session)

    script = request(client, 'POST', root + '/scripts', {'title':'雨停之前'}, status=201)['script']
    script_path = root + f"/scripts/{script['id']}"

    def working_version(text):
        current = request(client, 'GET', script_path)['script']
        created = request(client, 'POST', script_path + '/versions', {
            'expected_lock_version':current['lock_version'], 'text':text,
        }, status=201)
        version = created['version']
        request(client, 'POST', script_path + f"/versions/{version['id']}/confirm", {
            'expected_lock_version':version['lock_version'],
        })
        request(client, 'PUT', script_path + '/working-version', {
            'expected_lock_version':created['script']['lock_version'], 'version_id':version['id'],
        })
        return version

    source = working_version('走廊，日。\n林雨推开门，看向雨后的校园。')
    breakdown = request(client, 'POST', root + '/breakdowns', {'source_id':source['id']}, status=201)
    breakdown_path = root + f"/breakdowns/{breakdown['id']}"
    breakdown = request(client, 'PUT', breakdown_path, {
        'expected_lock_version':breakdown['lock_version'], 'scenes':[
            {'scene_number':'01','location_text':'教学楼走廊','time_of_day':'day',
             'source_start':1,'source_end':2,'characters':[{'name':'林雨'}]},
        ],
    })
    breakdown = request(client, 'POST', breakdown_path + '/submit', {'expected_lock_version':breakdown['lock_version']})
    breakdown = request(client, 'POST', breakdown_path + '/reviews', {
        'expected_lock_version':breakdown['lock_version'],'decision':'accept',
    })
    casting = request(client, 'GET', script_path + '/casting')
    request(client, 'PATCH', script_path + '/casting', {
        'expected_lock_version':casting['lock_version'], 'character_name':'林雨',
        'member_id':actor_id,'membership_joined_at':own['membership_joined_at'],
    })

    location = request(client, 'POST', root + '/resources', {
        'name':'教学楼走廊','resource_type':'location','unit':'处','total_quantity':1,'manager_member_id':manager['id'],
    }, status=201)
    location = request(client, 'PUT', root + f"/resources/{location['id']}/authorization", {
        'expected_lock_version':location['lock_version'],'manager_member_id':manager['id'],
        'expected_manager_lock_version':manager['lock_version'],'grant_review_permission':True,
    })
    reservation = request(client, 'POST', root + '/resource-reservations', {
        'resource_id':location['id'],'starts_at':start.isoformat(),'ends_at':end.isoformat(),'quantity':1,
    }, status=201)
    if reservation['status'] == 'pending':
        reservation = request(client, 'POST', root + f"/resource-reservations/{reservation['id']}/decisions", {
            'expected_lock_version':reservation['lock_version'],'decision':'accept',
        })

    plan = request(client, 'POST', root + '/shooting-plans', {'name':'第一拍摄日'}, status=201)
    draft = request(client, 'POST', root + f"/shooting-plans/{plan['id']}/versions", {}, status=201)
    plan_path = root + f"/plan-versions/{draft['id']}"
    draft = request(client, 'PUT', plan_path + '/sessions', {
        'expected_lock_version':draft['lock_version'],'sessions':[
            {'scene_revision_id':breakdown['items'][0]['id'],'starts_at':start.isoformat(),'ends_at':end.isoformat(),
             'location_resource_id':location['id'],'location_reservation_id':reservation['id'],
             'members':[{'member_id':actor_id,'membership_joined_at':own['membership_joined_at']}]},
        ],
    })
    proof = request(client, 'POST', plan_path + '/preflight', {'expected_lock_version':draft['lock_version']})
    assert proof['blockers'] == []
    assert proof['unknown_items'] == []
    publish_body = {'expected_lock_version':draft['lock_version'],
        'expected_current_published_version_id':proof['expected_current_published_version_id'],
        'checked_data_revision':proof['checked_data_revision'],'confirmed_unknown_item_ids':[]}
    key = str(uuid4())
    published = request(client, 'POST', plan_path + '/publish', publish_body, key=key)
    assert request(client, 'POST', plan_path + '/publish', publish_body, key=key) == published
    assert request(client, 'GET', root + f"/shooting-plans/{plan['id']}")['current_published_version_id'] == draft['id']

    sheet = request(client, 'POST', root + '/call-sheets', {
        'source_plan_version_id':draft['id'],'shooting_date':start.date().isoformat(),
    }, status=201)
    sheet_path = root + f"/call-sheet-versions/{sheet['id']}"
    sheet = request(client, 'PATCH', sheet_path, {'expected_lock_version':sheet['lock_version'],
        'gathering_at':(start-timedelta(minutes=30)).isoformat(),'gathering_location':'教学楼东侧入口'})
    sheet = request(client, 'POST', sheet_path + '/submit', {'expected_lock_version':sheet['lock_version']})
    sheet = request(client, 'POST', sheet_path + '/reviews', {'expected_lock_version':sheet['lock_version'],'decision':'accept'})
    sheet = request(client, 'POST', sheet_path + '/publish', {
        'expected_lock_version':sheet['lock_version'],'expected_current_published_version_id':None,
    })
    pdf = client.get('/api/v1' + sheet_path + '/export.pdf', params={'expected_lock_version':sheet['lock_version']})
    assert pdf.status_code == 200
    assert pdf.content.startswith(b'%PDF-')
    assert client.portal.call(consume_notices, sessions) == 2

    restore_session(client, actor_session)
    notices = request(client, 'GET', root + '/notifications')['items']
    actionable = [item for item in notices if item['requires_confirmation']]
    assert {item['source_type'] for item in actionable} == {'plan','call_sheet'}
    for item in actionable:
        confirmed = request(client, 'POST', root + f"/notifications/{item['id']}/acknowledge", {'object_version':item['object_version']})
        assert confirmed['confirmed_at'] is not None
    restore_session(client, director_session)
    for kind, version_id in [('plan', draft['id']), ('call_sheet', sheet['id'])]:
        receipts = request(client, 'GET', root + f'/notifications/receipts/{kind}/{version_id}')
        assert receipts['counts']['confirmed'] == 1
        assert receipts['counts']['unconfirmed'] == 0

    working_version('走廊，日。\n林雨推开门，停下来倾听远处的音乐。')
    assert 'script_changed' in request(client, 'GET', plan_path)['review_reasons']
    assert request(client, 'GET', sheet_path)['validity_status'] == 'review_required'
    changes = root + '/change-issues/breakdowns'
    first = request(client, 'POST', changes + '/checks', {}, status=201)
    assert first['detected_count'] == 1
    records = request(client, 'GET', changes + '/records')['items']
    assert len(records) == 1 and records[0]['status'] == 'open'
    issue_id = records[0]['id']
    again = request(client, 'POST', changes + '/checks', {}, status=201)
    assert again['detected_count'] == 0 and again['updated_count'] == 0
    assessment = request(client, 'GET', breakdown_path + '/source-assessment')
    reviewed = request(client, 'POST', breakdown_path + '/source-reviews', {
        'expected_lock_version':breakdown['lock_version'],
        'expected_working_version_id':assessment['working_script_version_id'],
        'expected_working_revision':assessment['working_revision'],'expected_review_id':None,
        'conclusion':'still_applicable','review_note':'剧本仅调整表演细节，场景与人员资源安排仍适用。',
    }, status=201)
    assert reviewed['status'] == 'still_applicable'
    closed = request(client, 'POST', changes + '/checks', {}, status=201)
    assert closed['resolved_count'] == 1 and closed['open_count'] == 0
    history = request(client, 'GET', changes + f'/records/{issue_id}/history')
    assert {item['kind'] for item in history['items']} == {'detected','resolved'}
    # Applicability review does not silently approve an already-published plan.
    assert 'script_changed' in request(client, 'GET', plan_path)['review_reasons']

    withdrawn = request(client, 'POST', plan_path + '/withdraw', {
        'expected_lock_version':published['lock_version'],'reason':'剧本调整后重新编排拍摄计划',
    })
    assert withdrawn['withdrawn_version_id'] == draft['id']
    assert request(client, 'GET', root + f"/shooting-plans/{plan['id']}")['current_published_version_id'] is None
    assert request(client, 'GET', sheet_path)['validity_status'] == 'withdrawn'

    async def persisted_profile():
        async with sessions() as session:
            user = await session.scalar(select(User).where(User.email_normalized == 'director@example.com'))
            return user.display_name
    assert client.portal.call(persisted_profile) == '导演同学'
