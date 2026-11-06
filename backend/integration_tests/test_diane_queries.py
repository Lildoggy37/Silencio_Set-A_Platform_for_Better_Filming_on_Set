"""Real business services and PostgreSQL, with only the external model simulated."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from langchain_core.messages import AIMessage

from silencio_set.diane.service import DianeService
from silencio_set.diane.tools import BusinessServices
from test_postgres_flow import login, request, seed_actor
from test_workflow_policies import seed


class ReadModel:
    def __init__(self, name, arguments):
        self.name, self.arguments, self.calls = name, arguments, []

    def bind_tools(self, tools): return self

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages.copy())
        if len(self.calls) == 1:
            return AIMessage(content='', tool_calls=[{'id': 'lookup', 'name': self.name, 'args': self.arguments}],
                usage_metadata={'input_tokens': 20, 'output_tokens': 10, 'total_tokens': 30})
        return AIMessage(content='已查询，详细依据见查询结果。',
            usage_metadata={'input_tokens': 30, 'output_tokens': 10, 'total_tokens': 40})


def test_diane_reads_real_business_data_with_crew_and_task_visibility(postgres_client):
    client = postgres_client
    state = client.app.state
    emails, member_user = client.portal.call(seed, state.auth.repository.sessions)
    login(client, emails[0])
    crew = request(client, 'POST', '/crews', {'name': 'Diane查询剧组', 'project_name': '筹备阶段'}, status=201)
    root = f"/crews/{crew['id']}"
    own = crew['membership']
    request(client, 'PUT', root + f"/members/{own['id']}/roles", {
        'expected_lock_version': own['lock_version'], 'role_codes': ['admin', 'director', 'producer']})
    client.portal.call(seed_actor, state.auth.repository.sessions, crew['id'], member_user)
    task = request(client, 'POST', root + '/tasks', {'title': '核对下次拍摄的收音设备',
        'owner_member_id': own['id'], 'requires_review': False,
        'due_at': (datetime.now(UTC) + timedelta(days=2)).isoformat()}, status=201)
    second = request(client, 'POST', '/crews', {'name': '另一个剧组', 'project_name': '不相关的作品'}, status=201)
    other_member = second['membership']
    request(client, 'PUT', f"/crews/{second['id']}/members/{other_member['id']}/roles", {
        'expected_lock_version': other_member['lock_version'], 'role_codes': ['admin', 'producer']})
    request(client, 'POST', f"/crews/{second['id']}/tasks", {'title': '其他剧组不应返回的任务',
        'owner_member_id': other_member['id'], 'requires_review': False,
        'due_at': (datetime.now(UTC) + timedelta(days=2)).isoformat()}, status=201)

    async def install(model):
        state.diane = DianeService(state.settings, BusinessServices(**{name: getattr(state, name)
            for name in BusinessServices.__dataclass_fields__}), model)

    try:
        model = ReadModel('list_tasks', {'page': 1})
        client.portal.call(install, model)
        result = request(client, 'POST', root + '/diane/queries', {'question': '还有哪些筹备任务没完成？'})
        assert result['status'] == 'completed' and result['usage']['total_tokens'] == 70
        content = model.calls[1][-1].content
        assert '核对下次拍摄的收音设备' in content and '其他剧组不应返回' not in content
        assert result['sources'][0]['kind'] == 'tasks' and result['tools'][0]['status'] == 'success'

        login(client, emails[1])
        denied_model = ReadModel('get_task', {'task_id': task['id']})
        client.portal.call(install, denied_model)
        denied = request(client, 'POST', root + '/diane/queries', {'question': '看看其他成员的任务'})
        assert denied['tools'][0]['status'] == 'denied' and not denied['sources']
        assert '核对下次拍摄的收音设备' not in denied_model.calls[1][-1].content
        assert request(client, 'GET', root + '/tasks')['total'] == 0
    finally:
        state.diane = None  # The scripted model owns no network clients.
