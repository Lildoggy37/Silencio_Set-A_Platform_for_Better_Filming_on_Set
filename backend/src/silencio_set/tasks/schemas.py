from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from silencio_set.crews.schemas import clean_text

TaskStatus = Literal["pending", "in_progress", "pending_review", "completed", "cancelled"]
TaskAction = Literal["start", "submit", "review", "edit", "cancel"]


def parse_due(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("Deadline must be an ISO date-time with timezone")
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class TaskCreate(Input):
    title: StrictStr
    description: StrictStr | None = None
    owner_member_id: UUID
    reviewer_member_id: UUID
    due_at: AwareDatetime
    collaborator_member_ids: list[UUID] = Field(default_factory=list, max_length=20)
    scene_id: None = None  # Reject non-null until the real scene model is available.

    @field_validator("due_at", mode="before")
    @classmethod
    def due_valid(cls, value):
        return parse_due(value)

    @field_validator("title")
    @classmethod
    def title_valid(cls, value):
        return clean_text(value, 100)

    @field_validator("description")
    @classmethod
    def description_valid(cls, value):
        return clean_text(value, 3000, multiline=True) if value is not None and value.strip() else None

    @field_validator("collaborator_member_ids")
    @classmethod
    def collaborators_valid(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Collaborators must be unique")
        return sorted(value)


class VersionInput(Input):
    expected_lock_version: int = Field(strict=True, ge=1, le=2_147_483_647)


class TaskPatch(VersionInput):
    title: StrictStr | None = None
    description: StrictStr | None = None
    owner_member_id: UUID | None = None
    reviewer_member_id: UUID | None = None
    due_at: AwareDatetime | None = None
    collaborator_member_ids: list[UUID] | None = Field(default=None, max_length=20)

    @field_validator("due_at", mode="before")
    @classmethod
    def due_valid(cls, value):
        return parse_due(value) if value is not None else None

    @model_validator(mode="after")
    def valid_changes(self):
        changes = self.model_dump(exclude_unset=True, exclude={"expected_lock_version"})
        if not changes or any(value is None for key, value in changes.items() if key != "description"):
            raise ValueError("Provide supported non-null changes")
        if "title" in changes:
            self.title = clean_text(self.title, 100)
        if "description" in changes:
            self.description = TaskCreate.description_valid(self.description)
        if "collaborator_member_ids" in changes:
            self.collaborator_member_ids = TaskCreate.collaborators_valid(self.collaborator_member_ids)
        return self


class SubmissionInput(VersionInput):
    completion_note: StrictStr

    @field_validator("completion_note")
    @classmethod
    def valid_note(cls, value):
        return clean_text(value, 2000, multiline=True)


class ReviewInput(VersionInput):
    submission_id: UUID
    decision: Literal["accept", "reject"]
    review_note: StrictStr | None = None

    @model_validator(mode="after")
    def valid_review(self):
        self.review_note = clean_text(self.review_note, 500, multiline=True) if self.review_note is not None and self.review_note.strip() else None
        if self.decision == "reject" and self.review_note is None:
            raise ValueError("Rejection requires a note")
        return self


class CancelInput(VersionInput):
    reason: StrictStr

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value):
        return clean_text(value, 500)


class TaskView(BaseModel):
    id: UUID
    crew_id: UUID
    title: str
    description: str | None
    owner_member_id: UUID
    reviewer_member_id: UUID
    created_by_member_id: UUID
    collaborator_member_ids: list[UUID]
    due_at: datetime
    status: TaskStatus
    cancel_reason: str | None
    lock_version: int
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[TaskAction]


class SubmissionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    submission_no: int
    submitted_by: UUID
    completion_note: str
    review_status: Literal["pending", "accepted", "rejected", "cancelled"]
    reviewed_by: UUID | None
    review_note: str | None
    submitted_at: datetime
    reviewed_at: datetime | None


class TaskDetail(BaseModel):
    task: TaskView
    member_names: dict[UUID, str]
    current_submission: SubmissionView | None
    submission_count: int


class TaskPage(BaseModel):
    items: list[TaskView]
    page: int
    page_size: int
    total: int


class SubmissionPage(BaseModel):
    items: list[SubmissionView]
    page: int
    page_size: int
    total: int


class ExitTaskItem(BaseModel):
    id: UUID
    title: str
    status: TaskStatus
    due_at: datetime
    lock_version: int
    is_owner: bool
    is_reviewer: bool


class ExitResponsibilityPage(BaseModel):
    member_id: UUID
    member_lock_version: int
    items: list[ExitTaskItem]
    total: int
    blocking_total: int
    restricted_total: int
    page: int
    page_size: int
