from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel


class ReceiptItem(BaseModel):
    member_id: UUID
    membership_joined_at: datetime
    display_name: str
    identity_status: Literal['active', 'left', 'rejoined', 'disabled']
    notification_status: Literal['pending_dispatch', 'generated', 'skipped']
    requires_confirmation: bool
    read_at: datetime | None
    confirmed_at: datetime | None


class ReceiptCounts(BaseModel):
    intended: int
    generated: int
    pending_dispatch: int
    skipped: int
    read: int
    confirmed: int
    unconfirmed: int


class ReceiptPage(BaseModel):
    source_type: Literal['plan', 'call_sheet']
    source_id: UUID
    version_no: int
    source_status: Literal['current', 'updated', 'review_required', 'superseded', 'withdrawn']
    observed_at: datetime
    dispatch_status: Literal['pending_dispatch', 'processed']
    counts: ReceiptCounts
    items: list[ReceiptItem]
    total: int
    page: int
    page_size: int
