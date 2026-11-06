from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from silencio_set.diane.schemas import QueryInput, QueryResult


class ConversationQuery(QueryInput):
    turn_id: UUID


class ConversationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class TurnView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ordinal: int
    question: str
    status: Literal['queued', 'running', 'completed', 'failed', 'interrupted']
    phase: str
    progress_version: int
    result: QueryResult | None
    created_at: datetime


class ConversationHistory(BaseModel):
    conversation: ConversationView
    turns: list[TurnView]
    has_earlier: bool


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid', from_attributes=True)
    response_style: Literal['concise', 'detailed'] = 'concise'
    focus: Literal['general', 'schedule', 'resources'] = 'general'
