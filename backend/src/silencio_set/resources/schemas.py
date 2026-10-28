from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from silencio_set.availability.schemas import TimeWindow
from silencio_set.crews.schemas import clean_text
from silencio_set.tasks.schemas import VersionInput

ResourceType = Literal["equipment", "prop", "location"]


class ResourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: StrictStr
    resource_type: ResourceType
    unit: StrictStr
    total_quantity: int = Field(strict=True, ge=1, le=1_000_000)
    manager_member_id: UUID
    description: StrictStr | None = None

    @field_validator("name", "unit")
    @classmethod
    def text_valid(cls, value, info):
        return clean_text(value, 100 if info.field_name == "name" else 10)

    @field_validator("description")
    @classmethod
    def description_valid(cls, value):
        return clean_text(value, 2000, multiline=True) if value is not None and value.strip() else None


class ResourcePatch(VersionInput):
    name: StrictStr | None = None
    unit: StrictStr | None = None
    total_quantity: int | None = Field(default=None, strict=True, ge=1, le=1_000_000)
    status: Literal["available", "disabled"] | None = None
    description: StrictStr | None = None

    @model_validator(mode="after")
    def valid_changes(self):
        changes = self.model_dump(exclude_unset=True, exclude={"expected_lock_version"})
        if not changes or any(value is None for key, value in changes.items() if key != "description"):
            raise ValueError("Provide supported non-null changes")
        if "name" in changes: self.name = clean_text(self.name, 100)
        if "unit" in changes: self.unit = clean_text(self.unit, 10)
        if "description" in changes: self.description = ResourceCreate.description_valid(self.description)
        return self


class ResourceAuthorization(VersionInput):
    manager_member_id: UUID
    expected_manager_lock_version: int = Field(strict=True, ge=1, le=2_147_483_647)
    grant_review_permission: bool = Field(default=False, strict=True)


class ReservationCreate(TimeWindow):
    resource_id: UUID
    quantity: int = Field(strict=True, ge=1, le=1_000_000)
    scene_id: None = None

    @model_validator(mode="after")
    def bounded(self):
        if (self.ends_at - self.starts_at).total_seconds() > 31 * 86400: raise ValueError("Reservation window exceeds 31 days")
        return self


class ReservationDecision(VersionInput):
    decision: Literal["accept", "reject"]
    decision_note: StrictStr | None = None

    @model_validator(mode="after")
    def valid_note(self):
        self.decision_note = clean_text(self.decision_note, 500, multiline=True) if self.decision_note is not None and self.decision_note.strip() else None
        if self.decision == "reject" and self.decision_note is None: raise ValueError("Rejection requires a reason")
        return self


class ReservationCancel(VersionInput):
    reason: StrictStr

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value): return clean_text(value, 500)


class ResourceView(BaseModel):
    id: UUID
    crew_id: UUID
    name: str
    resource_type: ResourceType
    unit: str
    total_quantity: int
    status: Literal["available", "disabled"]
    description: str | None
    manager_member_id: UUID
    manager_display_name: str
    manager_lock_version: int
    review_ready: bool
    lock_version: int
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[Literal["edit", "authorize", "apply"]]


class ResourcePage(BaseModel):
    items: list[ResourceView]
    total: int
    page: int
    page_size: int


class ReservationView(BaseModel):
    id: UUID
    crew_id: UUID
    resource_id: UUID
    applicant_member_id: UUID
    starts_at: datetime
    ends_at: datetime
    quantity: int
    status: Literal["pending", "confirmed", "rejected", "cancelled"]
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    decision_note: str | None
    cancelled_by: UUID | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    lock_version: int
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[Literal["accept", "reject", "cancel"]]
    dependency_check_status: Literal["not_implemented"] = "not_implemented"


class ReservationPage(BaseModel):
    items: list[ReservationView]
    total: int
    page: int
    page_size: int


class ResourceAvailability(BaseModel):
    resource_id: UUID
    resource_lock_version: int
    range_start: datetime
    range_end: datetime
    total_quantity: int
    peak_confirmed_quantity: int
    minimum_remaining_quantity: int
    confirmed_reservation_count: int
    physical_inventory_status: Literal["not_implemented"] = "not_implemented"


class ResourceResponsibilityItem(BaseModel):
    kind: Literal["resource", "reservation"]
    id: UUID
    resource_id: UUID
    title: str
    status: Literal["available", "disabled", "pending", "confirmed"]
    lock_version: int
    ends_at: datetime | None


class ResourceResponsibilities(BaseModel):
    member_id: UUID
    items: list[ResourceResponsibilityItem]
    total: int
    blocking_total: int
    restricted_total: int
    page: int
    page_size: int
