import asyncio
import json
from uuid import uuid4

from silencio_set.accounts.errors import AuthError
from silencio_set.core.config import Settings
from silencio_set.crews.schemas import MemberPage
from silencio_set.diane.mcp import mcp_tools
from silencio_set.diane.runtime import QueryRuntime
from silencio_set.diane.tools import build_tools
from test_crews import ready_user
from test_diane import ScriptedModel, call, message, services


def test_real_mcp_http_roundtrip_keeps_identity_validation_denials_and_evidence():
    async def scenario():
        business, user, crew = services(), ready_user(), uuid4()
        business.plans.execute.return_value = None
        business.tasks.execute.side_effect = AuthError(404, 'NOT_FOUND', '任务不可见')
        model = ScriptedModel(message(calls=[call('next_shoot'),
            call('get_task', {'task_id':str(uuid4())}), call('list_tasks', {'crew_id':str(uuid4())})]),
            message('尚未安排；该任务不可见。'))
        async with mcp_tools(build_tools(business, user, crew, 'mcp-test')) as tools:
            result = await QueryRuntime(Settings(_env_file=None), model, tools).run('查一下', crew)
        assert [item.status for item in result.tools] == ['success', 'denied', 'invalid']
        assert result.tools[1].error_code == 'NOT_FOUND'
        assert result.sources[0].kind == 'plans'
        assert business.plans.execute.await_args.kwargs['crew_id'] == crew
        assert business.tasks.execute.await_count == 1
    asyncio.run(scenario())


def test_members_tool_uses_authorized_directory_total_not_calendar_or_page_size():
    async def scenario():
        business, user, crew = services(), ready_user(), uuid4()
        business.crews.execute.return_value = MemberPage.model_validate({
            'items': [{'id': str(uuid4()), 'display_name': '摄影同学', 'role_codes': ['camera'],
                'status': 'active', 'lock_version': 1}],
            'total': 17, 'page': 1, 'page_size': 10})
        model = ScriptedModel(message(calls=[call('list_members'),
            call('list_members', {'crew_id': str(uuid4())})]), message('当前剧组有17名在组成员。'))
        async with mcp_tools(build_tools(business, user, crew, 'members-test')) as tools:
            result = await QueryRuntime(Settings(_env_file=None), model, tools).run('团队里有多少人？', crew)
        assert result.status == 'completed'
        assert result.sources[0].kind == 'members'
        assert [item.status for item in result.tools] == ['success', 'invalid']
        assert json.loads(model.calls[1][0][-2].content)['data']['total'] == 17
        business.crews.execute.assert_awaited_once_with(user, 'member.list', 'members-test',
            crew_id=crew, page=1, page_size=10)
        business.availability.execute.assert_not_awaited()
    asyncio.run(scenario())


def test_members_tool_preserves_directory_permission_denial():
    async def scenario():
        business, user, crew = services(), ready_user(), uuid4()
        business.crews.execute.side_effect = AuthError(404, 'NOT_FOUND', '成员目录不可见')
        model = ScriptedModel(message(calls=[call('list_members')]), message('当前无权读取成员目录。'))
        async with mcp_tools(build_tools(business, user, crew, 'members-denied')) as tools:
            result = await QueryRuntime(Settings(_env_file=None), model, tools).run('成员名单', crew)
        assert result.tools[0].status == 'denied' and not result.sources
        business.availability.execute.assert_not_awaited()
    asyncio.run(scenario())
