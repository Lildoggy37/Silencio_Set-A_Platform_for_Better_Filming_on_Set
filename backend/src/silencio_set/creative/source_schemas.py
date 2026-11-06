from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, StrictStr, field_validator
from silencio_set.crews.schemas import clean_text
from silencio_set.tasks.schemas import VersionInput


class SourceReviewInput(VersionInput):
    expected_working_version_id: UUID
    expected_working_revision: int = Field(strict=True, ge=1, le=2147483647)
    expected_review_id: UUID | None
    conclusion: Literal['still_applicable', 'needs_revision']
    review_note: StrictStr

    @field_validator('review_note')
    @classmethod
    def note(cls, value):
        return clean_text(value, 1000)


class SourceReviewView(BaseModel):
    model_config = {'from_attributes': True}
    id: UUID
    working_script_version_id: UUID
    working_revision: int
    target_lock_version: int
    reviewed_by: UUID
    conclusion: Literal['still_applicable', 'needs_revision']
    review_note: str
    reviewed_at: datetime


class SourceAssessment(BaseModel):
    kind: Literal['breakdowns', 'storyboards']
    id: UUID
    source_id: UUID
    version_no: int
    review_status: str
    lock_version: int
    script_id: UUID
    script_title: str
    source_script_version_id: UUID
    source_script_version_no: int
    working_script_version_id: UUID | None
    working_script_version_no: int | None
    working_revision: int
    status: Literal['current', 'review_required', 'still_applicable', 'needs_revision', 'no_working_version']
    latest_review: SourceReviewView | None


class SourceImpactPage(BaseModel):
    items: list[SourceAssessment]
    total: int
    page: int
    page_size: int


class SourceReviewPage(BaseModel):
    items: list[SourceReviewView]
    total: int
    page: int
    page_size: int
