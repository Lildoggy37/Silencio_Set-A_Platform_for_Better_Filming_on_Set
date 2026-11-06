from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictStr

from silencio_set.diane.schemas import QueryInput, QueryResult
from silencio_set.tasks.schemas import Input, TaskCreate, VersionInput


class DraftRequest(QueryInput):
    owner_member_id: UUID
    due_at: AwareDatetime


class DraftTask(Input):
    title: StrictStr = Field(min_length=1, max_length=100)
    description: StrictStr | None = Field(default=None, max_length=3000)


class DraftTasks(Input):
    tasks: list[DraftTask] = Field(min_length=1, max_length=5)


class ProposalInput(Input):
    tasks: list[TaskCreate] = Field(min_length=1, max_length=5)


class ProposalView(ProposalInput):
    id: UUID
    crew_id: UUID
    status: Literal['pending', 'completed', 'rejected']
    lock_version: int
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    created_task_ids: list[UUID]
    member_names: dict[str, str]


class ProposalPage(Input):
    items: list[ProposalView]


class ProposalDraftResult(Input):
    run: QueryResult
    proposal: ProposalView | None


class ProposalDecision(VersionInput):
    decision: Literal['accept', 'reject']
