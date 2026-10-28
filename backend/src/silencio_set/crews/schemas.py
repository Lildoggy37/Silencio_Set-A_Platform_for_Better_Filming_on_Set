import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from silencio_set.crews.permissions import ROLE_NAMES


def clean_text(value: str, maximum: int, *, multiline=False) -> str:
    value = value.strip()
    if not 1 <= len(value) <= maximum or any(
        unicodedata.category(char) in {"Cc", "Cs"} and not (multiline and char in "\n\t") for char in value
    ):
        raise ValueError("Text is empty, too long or contains unsupported control characters")
    return value


class CrewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: StrictStr
    project_name: StrictStr
    description: StrictStr | None = None

    @field_validator("name")
    @classmethod
    def name_valid(cls, value):
        return clean_text(value, 50)

    @field_validator("project_name")
    @classmethod
    def project_valid(cls, value):
        return clean_text(value, 100)

    @field_validator("description")
    @classmethod
    def description_valid(cls, value):
        if value is None or not value.strip():
            return None
        return clean_text(value, 1000, multiline=True)


class MemberView(BaseModel):
    id: UUID
    display_name: str
    status: Literal["active", "left", "removed"]
    role_codes: list[str]
    lock_version: int
    # No private email, user ID, password or account metadata in the directory.


class MemberRolesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    role_codes: list[StrictStr] = Field(max_length=8)
    expected_lock_version: int = Field(strict=True, ge=1, le=2_147_483_647)

    @field_validator("role_codes")
    @classmethod
    def valid_roles(cls, value):
        if len(value) != len(set(value)) or any(code not in ROLE_NAMES for code in value):
            raise ValueError("Role codes must be unique values from the fixed catalog")
        return sorted(value)


class MemberLeave(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    expected_lock_version: int = Field(strict=True, ge=1, le=2_147_483_647)


class MemberRemove(MemberLeave):
    reason: StrictStr

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value):
        return clean_text(value, 500)


class MemberExitResult(BaseModel):
    membership: MemberView
    ended_at: datetime


class CrewView(BaseModel):
    id: UUID
    name: str
    project_name: str
    description: str | None
    created_at: datetime
    membership: MemberView
    permissions: list[str]


class CrewPage(BaseModel):
    items: list[CrewView]
    page: int
    page_size: int
    total: int


class MemberPage(BaseModel):
    items: list[MemberView]
    page: int
    page_size: int
    total: int
