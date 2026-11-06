from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class QueryInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    question: StrictStr = Field(min_length=1, max_length=4000)

    @field_validator('question')
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value: raise ValueError('A question is required')
        return value


class DianeStatus(BaseModel):
    enabled: bool
    model: str | None = None
    mode: Literal['queries_and_task_proposals'] = 'queries_and_task_proposals'
    timeout_seconds: int
    execution_mode: Literal['synchronous', 'background'] = 'synchronous'


class Evidence(BaseModel):
    kind: Literal['tasks', 'task', 'resources', 'resource', 'plans', 'plan', 'plan_version', 'availability', 'creative', 'changes']
    label: str
    record_id: UUID | None = None


class ToolObservation(BaseModel):
    name: str
    status: Literal['success', 'denied', 'invalid', 'unavailable', 'too_large']
    elapsed_ms: int
    error_code: str | None = None
    source: Evidence | None = None


class ModelObservation(BaseModel):
    elapsed_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    status: Literal['completed', 'failed'] = 'completed'


class QueryUsage(BaseModel):
    elapsed_ms: int
    model_ms: int
    tool_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    usage_complete: bool
    budget_charged_tokens: int
    budget_contains_estimates: bool


class QueryResult(BaseModel):
    run_id: UUID
    status: Literal['completed', 'budget_exceeded', 'failed', 'timed_out']
    answer: str
    stop_reason: str | None = None
    tools: list[ToolObservation]
    model_calls: list[ModelObservation]
    sources: list[Evidence]
    usage: QueryUsage
