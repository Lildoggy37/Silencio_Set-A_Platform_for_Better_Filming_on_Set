from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator

from silencio_set.creative.schemas import CharacterInput
from silencio_set.tasks.schemas import VersionInput


class CastingSave(VersionInput):
    character_name: str
    member_id: UUID | None
    membership_joined_at: datetime | None

    @field_validator('character_name', mode='before')
    @classmethod
    def name(cls, value):
        return CharacterInput(name=value).name

    @model_validator(mode='after')
    def identity(self):
        if (self.member_id is None) != (self.membership_joined_at is None):
            raise ValueError('Member and membership epoch must be provided together')
        if self.membership_joined_at is not None and self.membership_joined_at.utcoffset() is None:
            raise ValueError('Timezone required')
        return self


class CastingEntry(BaseModel):
    character_name: str
    is_current_character: bool
    member_id: UUID | None
    membership_joined_at: datetime | None
    display_name: str | None
    status: Literal['unassigned', 'active', 'invalid']


class CastingPage(BaseModel):
    script_id: UUID
    lock_version: int
    items: list[CastingEntry]
    total: int
    page: int
    page_size: int


class CastingResult(BaseModel):
    script_id: UUID
    lock_version: int
    character_name: str
    member_id: UUID | None
    membership_joined_at: datetime | None
    display_name: str | None


class ActorChoice(BaseModel):
    id: UUID
    display_name: str
    membership_joined_at: datetime


class ActorPage(BaseModel):
    items: list[ActorChoice]
    total: int
    page: int
    page_size: int


class CastingScript(BaseModel):
    id: UUID
    title: str


class CastingScriptPage(BaseModel):
    items: list[CastingScript]
    total: int
    page: int
    page_size: int
