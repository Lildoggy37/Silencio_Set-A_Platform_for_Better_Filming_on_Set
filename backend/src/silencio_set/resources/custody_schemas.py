from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, StrictStr, field_validator
from silencio_set.crews.schemas import clean_text
from silencio_set.tasks.schemas import VersionInput, parse_due


class CheckoutInput(VersionInput):
    quantity: int = Field(strict=True, ge=1, le=1_000_000)
    expected_return_at: AwareDatetime

    @field_validator("expected_return_at", mode="before")
    @classmethod
    def aware(cls, value): return parse_due(value)


class ReturnInput(VersionInput):
    quantity: int = Field(strict=True, ge=1, le=1_000_000)
    note: StrictStr | None = None

    @field_validator("note")
    @classmethod
    def note_valid(cls, value): return clean_text(value, 500, multiline=True) if value is not None and value.strip() else None


class CustodyAuthorization(VersionInput):
    member_id: UUID
    expected_member_lock_version: int = Field(strict=True, ge=1, le=2_147_483_647)
    permission_codes: list[Literal["resource.checkout", "resource.return"]] = Field(max_length=2)
    grant_capabilities: bool = Field(default=False, strict=True)

    @field_validator("permission_codes")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)): raise ValueError("Duplicate permissions")
        return sorted(value)


class CustodyAuthorizationView(BaseModel):
    resource_id: UUID
    member_id: UUID
    resource_lock_version: int
    member_lock_version: int
    permission_codes: list[str]


class CheckoutView(BaseModel):
    id: UUID
    crew_id: UUID
    resource_id: UUID
    reservation_id: UUID
    borrower_member_id: UUID
    quantity: int
    returned_quantity: int
    outstanding_quantity: int
    checked_out_at: datetime
    expected_return_at: datetime
    handled_by: UUID
    lock_version: int
    status: Literal["outstanding", "overdue", "returned"]


class CheckoutPage(BaseModel):
    items: list[CheckoutView]
    total: int
    page: int
    page_size: int
