from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel

Category = Literal['breakdowns', 'storyboards', 'plans', 'call_sheets']


class ChangeIssue(BaseModel):
    id: UUID
    category: Category
    parent_id: UUID
    source_id: UUID | None
    container_id: UUID | None = None
    current_plan_version_id: UUID | None = None
    title: str
    status: str
    reason_codes: list[str]


class ChangeIssuePage(BaseModel):
    category: Category
    allowed_categories: list[Category]
    observed_at: datetime
    items: list[ChangeIssue]
    total: int
    page: int
    page_size: int


class ImpactCheckView(BaseModel):
    model_config = {'from_attributes': True}
    id: UUID
    category: Category
    checked_at: datetime
    detected_count: int
    updated_count: int
    resolved_count: int
    reopened_count: int
    open_count: int


class RecordedIssue(BaseModel):
    id: UUID
    target: ChangeIssue
    status: Literal['open','resolved']
    first_detected_at: datetime
    last_checked_at: datetime
    resolved_at: datetime | None
    reopened_count: int
    lock_version: int
    resolution_reason: str | None


class RecordedIssuePage(BaseModel):
    items: list[RecordedIssue]
    latest_check: ImpactCheckView | None
    total: int
    page: int
    page_size: int


class ImpactTransitionView(BaseModel):
    id: UUID
    check_id: UUID
    kind: Literal['detected','updated','resolved','reopened']
    recorded_at: datetime
    before_status: str | None
    after_status: str
    target: ChangeIssue
    evidence: dict
    resolution_reason: str | None


class ImpactHistoryPage(BaseModel):
    issue: RecordedIssue
    items: list[ImpactTransitionView]
    total: int
    page: int
    page_size: int
