import asyncio
from uuid import uuid4

from silencio_set.accounts.errors import AuthError
from silencio_set.core.config import Settings
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
