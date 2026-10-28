from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator

from silencio_set.accounts.schemas import AccountInput, EmailInput
from silencio_set.crews.schemas import MemberView

InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]


class InvitationCreate(EmailInput):
    pass


class InvitationRevoke(AccountInput):
    expected_lock_version: int = Field(strict=True, ge=1, le=2_147_483_647)


class InvitationAccept(AccountInput):
    token: SecretStr = Field(repr=False)

    @field_validator("token", mode="before")
    @classmethod
    def valid_token(cls, value):
        import re
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
            raise ValueError("Invalid invitation credential")
        return value


class InvitationView(BaseModel):
    id: UUID
    email: str
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime
    lock_version: int


class InvitationPage(BaseModel):
    items: list[InvitationView]
    page: int
    page_size: int
    total: int


class InvitationCreated(BaseModel):
    invitation: InvitationView
    delivery_status: Literal["pending_dispatch"] = "pending_dispatch"


class InvitationJoined(BaseModel):
    crew_id: UUID
    membership: MemberView
    accepted: Literal[True] = True
