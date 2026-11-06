from datetime import datetime, timedelta
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, StrictStr, field_validator, model_validator

from silencio_set.availability.schemas import TimeWindow
from silencio_set.creative.schemas import Input
from silencio_set.crews.schemas import clean_text
from silencio_set.tasks.schemas import VersionInput, parse_due


class PlanCreate(Input):
    name: StrictStr

    @field_validator('name')
    @classmethod
    def name_valid(cls, value):
        return clean_text(value, 100)


class VersionCreate(Input):
    copy_from: UUID | None = None


class MemberInput(Input):
    member_id: UUID
    membership_joined_at: AwareDatetime

    @field_validator('membership_joined_at', mode='before')
    @classmethod
    def joined_valid(cls, value):
        return parse_due(value)


class ResourceInput(Input):
    resource_id: UUID
    quantity: int = Field(strict=True, ge=1, le=1000000)
    reservation_id: UUID | None = None


class SessionInput(TimeWindow):
    scene_revision_id: UUID
    location_resource_id: UUID | None = None
    location_reservation_id: UUID | None = None
    transition_minutes: int = Field(default=0, strict=True, ge=0, le=1440)
    note: StrictStr | None = None
    members: list[MemberInput] = Field(default_factory=list, max_length=100)
    resources: list[ResourceInput] = Field(default_factory=list, max_length=100)

    @field_validator('note')
    @classmethod
    def note_valid(cls, value):
        return clean_text(value, 2000, multiline=True) if value is not None and value.strip() else None

    @model_validator(mode='after')
    def references_valid(self):
        if len({item.member_id for item in self.members}) != len(self.members):
            raise ValueError('Duplicate session member')
        if len({item.resource_id for item in self.resources}) != len(self.resources):
            raise ValueError('Duplicate session resource')
        if self.location_resource_id in {item.resource_id for item in self.resources}:
            raise ValueError('Location is allocated separately')
        if self.location_reservation_id and self.location_resource_id is None:
            raise ValueError('Location reservation requires a location')
        # Limit the date range before adding preparation minutes.
        if self.ends_at.year >= 9999 or self.ends_at - self.starts_at > timedelta(days=31):
            raise ValueError('Session exceeds supported time range')
        return self


class SessionsSave(VersionInput):
    sessions: list[SessionInput] = Field(max_length=100)

    @model_validator(mode='after')
    def bounded(self):
        if self.sessions and max(item.ends_at for item in self.sessions) - min(item.starts_at for item in self.sessions) > timedelta(days=31):
            raise ValueError('Split plans spanning more than 31 days')
        return self


class PlanView(BaseModel):
    model_config = {'from_attributes': True}
    id: UUID
    name: str
    created_at: datetime
    current_published_version_id: UUID | None = None


class PlanPage(BaseModel):
    items: list[PlanView]
    total: int
    page: int
    page_size: int


class VersionSummary(BaseModel):
    model_config = {'from_attributes': True}
    id: UUID
    plan_id: UUID
    source_version_id: UUID | None
    version_no: int
    status: str
    lock_version: int
    published_at: datetime | None = None
    withdrawn_at: datetime | None = None
    withdrawal_reason: str | None = None


class VersionPage(BaseModel):
    items: list[VersionSummary]
    total: int
    page: int
    page_size: int


class VersionView(VersionSummary):
    sessions: list[dict]
    review_reasons: list[str] = Field(default_factory=list)


class CheckIssue(BaseModel):
    code: str
    message: str
    session_id: UUID | None = None
    member_id: UUID | None = None
    resource_id: UUID | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class UnknownItem(CheckIssue):
    item_id: UUID


class PreflightView(BaseModel):
    checked_lock_version: int
    blockers: list[CheckIssue]
    unknown_items: list[CheckIssue]
    warnings: list[CheckIssue] = Field(default_factory=list)
    can_publish: bool = False
    limitations: list[str]
    checked_data_revision: str | None = None
    expected_current_published_version_id: UUID | None = None
    expires_at: datetime | None = None


class PublicationPreflight(PreflightView):
    unknown_items: list[UnknownItem]


class PublishInput(VersionInput):
    expected_current_published_version_id: UUID | None
    checked_data_revision: StrictStr = Field(min_length=32, max_length=128)
    confirmed_unknown_item_ids: list[UUID] = Field(max_length=10000)

    @model_validator(mode='after')
    def unique_confirmations(self):
        if len(set(self.confirmed_unknown_item_ids)) != len(self.confirmed_unknown_item_ids):
            raise ValueError('Duplicate confirmation')
        return self


class PublishView(BaseModel):
    published_version_id: UUID
    status: str = 'published'
    lock_version: int
    previous_published_version_id: UUID | None
    affected_call_sheet_version_ids: list[UUID] = Field(default_factory=list)
    notification_status: str = 'pending_dispatch'


class WithdrawInput(VersionInput):
    reason: StrictStr

    @field_validator('reason')
    @classmethod
    def reason_text(cls, value):
        return clean_text(value, 500)


class WithdrawView(BaseModel):
    withdrawn_version_id: UUID
    lock_version: int
    withdrawn_at: datetime
    withdrawal_reason: str
    affected_call_sheet_version_ids: list[UUID]
    notification_status: str = 'pending_dispatch'


class NextSession(BaseModel):
    session_id: UUID
    plan_name: str
    scene_label: str
    location_name: str
    starts_at: datetime
    ends_at: datetime
    review_reasons: list[str]


class Choice(BaseModel):
    id: UUID
    label: str
    resource_type: str | None = None
    unit: str | None = None
    membership_joined_at: datetime | None = None
    quantity: int | None = None


class ChoicePage(BaseModel):
    items: list[Choice]
    total: int
    page: int
    page_size: int
