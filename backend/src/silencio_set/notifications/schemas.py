from datetime import datetime
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class AcknowledgeInput(EmptyInput):
    object_version: int = Field(strict=True, ge=1, le=2_147_483_647)


class NotificationView(BaseModel):
    id: UUID
    event_type: str
    title: str
    summary: str
    created_at: datetime
    source_type: Literal['task', 'plan', 'call_sheet']
    source_id: UUID
    object_title: str
    object_version: int
    current_object_version: int | None
    is_current: bool
    source_status: Literal['current', 'updated', 'review_required', 'superseded', 'withdrawn']
    can_open_source: bool
    requires_confirmation: bool
    read_at: datetime | None
    confirmed_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationView]
    page: int
    page_size: int
    total: int
