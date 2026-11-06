"""Tool schemas contain business filters only. Identity is bound by the server."""
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from silencio_set.availability.schemas import QueryWindow
from silencio_set.changes.schemas import Category
from silencio_set.diane.schemas import Evidence


class PageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page: int = Field(default=1, ge=1, le=100)


class TaskFilter(PageInput):
    status: Literal['pending', 'in_progress', 'pending_review', 'completed', 'cancelled'] | None = None


class TaskId(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: UUID


class ResourceId(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource_id: UUID


class VersionId(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_id: UUID


class ResourceWindow(QueryWindow):
    resource_id: UUID


class CalendarWindow(QueryWindow):
    page: int = Field(default=1, ge=1, le=100)


class ChangeFilter(PageInput):
    category: Category


class CreativeId(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['breakdowns', 'storyboards']
    object_id: UUID


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


@dataclass(frozen=True)
class BusinessServices:
    crews: object
    tasks: object
    resources: object
    plans: object
    availability: object
    creative: object
    changes: object


def observed(data, kind, label, record_id=None):
    return {'data': data.model_dump(mode='json') if isinstance(data, BaseModel) else data,
            'source': Evidence(kind=kind, label=label, record_id=record_id).model_dump(mode='json')}


def build_tools(services: BusinessServices, user, crew_id, request_id):
    # Every invocation goes through the same authorized service as the page.
    @tool(args_schema=TaskFilter)
    async def list_tasks(page=1, status=None):
        """分页查询当前剧组中本人可见的筹备任务，可按状态过滤。不能据单页推断全量。"""
        data = await services.tasks.execute(user, 'list', request_id, crew_id=crew_id, page=page, page_size=10, status=status)
        payload = data.model_dump(mode='json')
        for item in payload['items']: item.pop('description', None)
        return observed(payload, 'tasks', '筹备任务列表')

    @tool(args_schema=TaskId)
    async def get_task(task_id):
        """读取当前剧组内一项可见任务的说明、状态和当前成果。"""
        data = await services.tasks.execute(user, 'read', request_id, crew_id=crew_id, task_id=task_id)
        return observed(data, 'task', data.task.title, data.task.id)

    @tool(args_schema=PageInput)
    async def list_resources(page=1):
        """分页查看当前剧组的设备、道具、场地目录。登记数量不代表某个时段可用。"""
        data = await services.resources.execute(user, 'list', request_id, crew_id=crew_id, page=page, page_size=10)
        return observed(data, 'resources', '资源目录')

    @tool(args_schema=ResourceId)
    async def get_resource(resource_id):
        """读取当前剧组一项资源的登记状态、数量和预约方式。"""
        data = await services.resources.execute(user, 'get', request_id, crew_id=crew_id, resource_id=resource_id)
        return observed(data, 'resource', data.name, data.id)

    @tool(args_schema=ResourceWindow)
    async def resource_availability(resource_id, range_start, range_end):
        """查询一个不超过31天的带时区整秒区间内资源余量；不创建或确认预约。"""
        window = QueryWindow(range_start=range_start, range_end=range_end)
        data = await services.resources.execute(user, 'availability', request_id, crew_id=crew_id, resource_id=resource_id, window=window)
        return observed(data, 'resource', '资源时段余量', resource_id)

    @tool(args_schema=PageInput)
    async def list_plans(page=1):
        """分页查询本人可见拍摄计划和当前正式版本标识。历史版本不等于当前安排。"""
        data = await services.plans.execute(user, 'read', request_id, crew_id=crew_id, page=page, page_size=10)
        return observed(data, 'plans', '拍摄计划列表')

    @tool(args_schema=VersionId)
    async def get_plan_version(version_id):
        """查看当前剧组可见的一个排期版本，包括场戏、时间、人员及资源；需核对版本是否当前有效。"""
        data = await services.plans.execute(user, 'read', request_id, crew_id=crew_id, version_id=version_id)
        plan = await services.plans.execute(user, 'get_plan', request_id, crew_id=crew_id, plan_id=data.plan_id)
        return observed({'version': data.model_dump(mode='json'), 'plan': plan.model_dump(mode='json')}, 'plan_version', f'{plan.name} · 第{data.version_no}版', data.id)

    @tool(args_schema=EmptyInput)
    async def next_shoot():
        """查询本人可见的下一场正式拍摄安排；没有数据时明确待安排。"""
        data = await services.plans.execute(user, 'next', request_id, crew_id=crew_id)
        return observed(data, 'plans', '下一次正式拍摄')

    @tool(args_schema=CalendarWindow)
    async def crew_availability(range_start, range_end, page=1):
        """分页查询有权限查看的剧组档期，范围不超过31天。未登记为未知，不能当作可用。"""
        window = QueryWindow(range_start=range_start, range_end=range_end)
        data = await services.availability.execute(user, 'list', request_id, crew_id=crew_id, window=window, page=page, page_size=10)
        return observed(data, 'availability', '剧组档期')

    @tool(args_schema=CreativeId)
    async def get_creative_draft(kind, object_id):
        """读取有权限查看的场戏拆解或文字分镜；包含来源及确认状态。草稿不是正式安排。"""
        data = await services.creative.execute(user, 'read', request_id, crew_id=crew_id, kind=kind, object_id=object_id)
        return observed(data, 'creative', '场戏拆解' if kind == 'breakdowns' else '文字分镜', object_id)

    @tool(args_schema=ChangeFilter)
    async def list_preparation_issues(category, page=1):
        """只读查询场戏、分镜、排期或通告因已录入信息变化而需要复核的事项，不是全面筹备验收。"""
        data = await services.changes.list(user, crew_id, request_id, category=category, page=page, page_size=10)
        return observed(data, 'changes', '需要重新确认的安排')

    return [list_tasks, get_task, list_resources, get_resource, resource_availability,
            list_plans, get_plan_version, next_shoot, crew_availability, get_creative_draft, list_preparation_issues]
