from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, StrictStr, field_validator, model_validator

from silencio_set.creative.schemas import Input
from silencio_set.crews.schemas import clean_text
from silencio_set.tasks.schemas import VersionInput, parse_due


class CallSheetCreate(Input):
    source_plan_version_id: UUID
    shooting_date: date

    @field_validator('shooting_date', mode='before')
    @classmethod
    def date_valid(cls, value):
        if isinstance(value, str): return date.fromisoformat(value)
        if type(value) is date: return value
        raise ValueError('Use an ISO calendar date')


class CallSheetVersionCreate(Input):
    source_plan_version_id: UUID
    copy_from: UUID | None = None


class CallSheetEdit(VersionInput):
    gathering_at: AwareDatetime | None = None
    gathering_location: StrictStr | None = None
    contact_note: StrictStr | None = None
    notes: StrictStr | None = None

    @model_validator(mode='after')
    def changed_fields(self):
        if not self.model_fields_set - {'expected_lock_version'}: raise ValueError('No fields to change')
        return self

    @field_validator('gathering_at', mode='before')
    @classmethod
    def time_valid(cls, value):
        return parse_due(value) if value is not None else None

    @field_validator('gathering_at')
    @classmethod
    def seconds_valid(cls, value):
        if value is not None and value.microsecond: raise ValueError('Whole seconds required')
        return value.astimezone(UTC) if value is not None else None

    @field_validator('gathering_location', 'contact_note', 'notes')
    @classmethod
    def text_valid(cls, value, info):
        if value is None or not value.strip(): return None
        return clean_text(value, {'gathering_location':300, 'contact_note':500, 'notes':2000}[info.field_name], multiline=info.field_name != 'gathering_location')


class RecipientEdit(VersionInput):
    member_ids: list[UUID] = Field(max_length=10000)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.member_ids)) != len(self.member_ids): raise ValueError('Duplicate recipient')
        return self


class CallSheetPublish(VersionInput):
    expected_current_published_version_id: UUID | None


class SheetView(BaseModel):
    model_config = {'from_attributes': True}
    id: UUID
    plan_id: UUID
    shooting_date: date
    current_published_version_id: UUID | None


class SheetPage(BaseModel):
    items: list[SheetView]
    total: int
    page: int
    page_size: int


class VersionSummary(BaseModel):
    model_config = {'from_attributes': True}
    id: UUID
    call_sheet_id: UUID
    source_plan_version_id: UUID
    version_no: int
    lock_version: int
    status: str
    validity_status: str | None


class RecipientView(BaseModel):
    member_id: UUID
    display_name: str
    required: bool


class VersionView(VersionSummary):
    call_sheet: SheetView
    source_current: bool
    gathering_at: datetime | None
    gathering_location: str | None
    contact_note: str | None
    notes: str | None
    arrangement_snapshot: dict
    recipients: list[RecipientView]
    reviewed_at: datetime | None
    published_at: datetime | None
    review_id: UUID | None = None
    notification_status: str | None = None


class ExportSnapshot(BaseModel):
    version: VersionView
    exported_at: datetime


class VersionPage(BaseModel):
    items: list[VersionSummary]
    total: int
    page: int
    page_size: int


class ReviewView(BaseModel):
    model_config = {'from_attributes': True}
    id: UUID
    reviewed_by: UUID
    reviewed_at: datetime
    decision: str
    review_note: str | None
    reviewed_lock_version: int
    content_snapshot: dict


class ReviewPage(BaseModel):
    items: list[ReviewView]
    total: int
    page: int
    page_size: int


class RecipientOption(BaseModel):
    member_id: UUID
    display_name: str
    required: bool


class RecipientPage(BaseModel):
    items: list[RecipientOption]
    total: int
    page: int
    page_size: int
