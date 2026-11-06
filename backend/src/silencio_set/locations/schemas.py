from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator, model_validator

from silencio_set.crews.schemas import clean_text
from silencio_set.tasks.schemas import VersionInput


class LocationDetailsView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    address: str | None = None
    access_instructions: str | None = None
    shooting_constraints: str | None = None


class LocationDetailsEdit(VersionInput):
    address: StrictStr | None = None
    access_instructions: StrictStr | None = None
    shooting_constraints: StrictStr | None = None

    @field_validator('address', 'access_instructions', 'shooting_constraints')
    @classmethod
    def text_valid(cls, value, info):
        if value is None or not value.strip(): return None
        return clean_text(value, {'address':300,'access_instructions':1000,'shooting_constraints':2000}[info.field_name], multiline=info.field_name != 'address')

    @model_validator(mode='after')
    def changed(self):
        if not self.model_fields_set - {'expected_lock_version'}: raise ValueError('No changed fields')
        return self


class PhotoDescriptionEdit(VersionInput):
    description: StrictStr | None

    @field_validator('description')
    @classmethod
    def text_valid(cls, value):
        return clean_text(value, 500, multiline=True) if value is not None and value.strip() else None


class PhotoUploadIdentity(PhotoDescriptionEdit):
    source_sha256: str


class PhotoView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    description: str | None
    file_format: Literal['jpeg', 'png', 'webp']
    byte_size: int
    width_px: int
    height_px: int
    created_at: datetime


class LocationView(BaseModel):
    resource_id: UUID
    resource_name: str
    resource_status: str
    resource_lock_version: int
    details: LocationDetailsView
    photos: list[PhotoView]
    allowed_actions: list[Literal['edit_details', 'upload_photo', 'edit_photo', 'remove_photo']]
