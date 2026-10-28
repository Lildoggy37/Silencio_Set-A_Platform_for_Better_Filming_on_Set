from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from silencio_set.tasks.schemas import parse_due


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class TimeWindow(Input):
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @field_validator("starts_at", "ends_at", mode="before")
    @classmethod
    def iso_format(cls, value):
        return parse_due(value)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def seconds_precision(cls, value):
        if value.microsecond: raise ValueError("Availability supports whole seconds")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def positive(self):
        if self.ends_at <= self.starts_at: raise ValueError("End must be after start")
        return self


class AvailabilityInterval(TimeWindow):
    availability: Literal["available", "unavailable"]


class QueryWindow(Input):
    range_start: AwareDatetime
    range_end: AwareDatetime

    @field_validator("range_start", "range_end", mode="before")
    @classmethod
    def iso_format(cls, value):
        return parse_due(value)

    @field_validator("range_start", "range_end")
    @classmethod
    def seconds_precision(cls, value):
        return TimeWindow.seconds_precision(value)

    @model_validator(mode="after")
    def bounded(self):
        if not timedelta(0) < self.range_end - self.range_start <= timedelta(days=31):
            raise ValueError("Window must be positive and at most 31 days")
        return self


class ReplaceAvailability(QueryWindow):
    expected_lock_version: int = Field(strict=True, ge=1, le=2_147_483_647)
    expected_membership_joined_at: AwareDatetime
    intervals: list[AvailabilityInterval] = Field(max_length=100)

    @field_validator("expected_membership_joined_at", mode="before")
    @classmethod
    def membership_iso(cls, value):
        return parse_due(value)

    @model_validator(mode="after")
    def valid_intervals(self):
        self.intervals.sort(key=lambda item: item.starts_at)
        previous_end = self.range_start
        for item in self.intervals:
            if item.starts_at < self.range_start or item.ends_at > self.range_end or item.starts_at < previous_end:
                raise ValueError("Intervals must be disjoint and inside the replacement window")
            previous_end = item.ends_at
        return self


class AvailabilityView(BaseModel):
    member_id: UUID
    display_name: str
    membership_joined_at: datetime
    lock_version: int
    updated_at: datetime | None
    range_start: datetime
    range_end: datetime
    intervals: list[AvailabilityInterval]
    unregistered: list[TimeWindow]
    plan_review_status: Literal["not_implemented"] = "not_implemented"


class AvailabilityPage(BaseModel):
    items: list[AvailabilityView]
    total: int
    page: int
    page_size: int
