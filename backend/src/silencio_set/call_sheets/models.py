from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class CallSheet(Base):
    __tablename__ = 'call_sheets'
    __table_args__ = (
        UniqueConstraint('crew_id', 'id', name='uq_call_sheet_scope'),
        UniqueConstraint('crew_id', 'id', 'plan_id', name='uq_call_sheet_plan'),
        UniqueConstraint('plan_id', 'shooting_date', name='uq_call_sheet_date'),
        ForeignKeyConstraint(['crew_id', 'plan_id'], ['shooting_plans.crew_id', 'shooting_plans.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'id', 'current_published_version_id'], ['call_sheet_versions.crew_id', 'call_sheet_versions.call_sheet_id', 'call_sheet_versions.id'], use_alter=True, name='fk_call_sheet_current_version', ondelete='RESTRICT'),
        Index('ix_call_sheets_crew_date', 'crew_id', 'shooting_date', 'id'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    plan_id: Mapped[UUID]
    shooting_date: Mapped[date] = mapped_column(Date)
    current_published_version_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallSheetVersion(Base):
    __tablename__ = 'call_sheet_versions'
    __table_args__ = (
        UniqueConstraint('crew_id', 'id', name='uq_call_sheet_version_scope'),
        UniqueConstraint('crew_id', 'call_sheet_id', 'id', name='uq_call_sheet_version_parent'),
        UniqueConstraint('call_sheet_id', 'version_no', name='uq_call_sheet_version_number'),
        ForeignKeyConstraint(['crew_id', 'call_sheet_id', 'plan_id'], ['call_sheets.crew_id', 'call_sheets.id', 'call_sheets.plan_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'plan_id', 'source_plan_version_id'], ['shooting_plan_versions.crew_id', 'shooting_plan_versions.plan_id', 'shooting_plan_versions.id'], ondelete='RESTRICT'),
        *(ForeignKeyConstraint(['crew_id', field], ['crew_members.crew_id', 'crew_members.id'], name=f'fk_call_sheet_{field}', ondelete='RESTRICT') for field in ('created_by', 'reviewed_by', 'published_by')),
        CheckConstraint("status IN ('draft','pending_review','approved','published')", name='status_valid'),
        CheckConstraint("(status = 'published' AND validity_status IS NOT NULL AND validity_status IN ('valid','review_required','superseded','withdrawn')) OR (status <> 'published' AND validity_status IS NULL)", name='validity_valid'),
        CheckConstraint("status NOT IN ('approved','published') OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND approved_content_digest IS NOT NULL)", name='approved_valid'),
        CheckConstraint("status <> 'published' OR (published_by IS NOT NULL AND published_at IS NOT NULL)", name='published_valid'),
        CheckConstraint('version_no > 0 AND lock_version BETWEEN 1 AND 2147483647', name='version_valid'),
        Index('ix_call_sheet_versions_source', 'crew_id', 'source_plan_version_id', 'id'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    call_sheet_id: Mapped[UUID]
    plan_id: Mapped[UUID]
    source_plan_version_id: Mapped[UUID]
    version_no: Mapped[int] = mapped_column(Integer)
    lock_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    validity_status: Mapped[str | None] = mapped_column(String(20))
    gathering_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gathering_location: Mapped[str | None] = mapped_column(String(300))
    contact_note: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(String(2000))
    arrangement_snapshot: Mapped[dict] = mapped_column(JSONB)
    approved_content_digest: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[UUID]
    reviewed_by: Mapped[UUID | None]
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[UUID | None]
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DraftRecipient(Base):
    __tablename__ = 'call_sheet_draft_recipients'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'call_sheet_version_id'], ['call_sheet_versions.crew_id', 'call_sheet_versions.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['crew_id', 'member_id'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
    )
    crew_id: Mapped[UUID]
    call_sheet_version_id: Mapped[UUID] = mapped_column(primary_key=True)
    member_id: Mapped[UUID] = mapped_column(primary_key=True)
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str] = mapped_column(String(30))


class PublishedRecipient(Base):
    __tablename__ = 'call_sheet_published_recipients'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'call_sheet_version_id'], ['call_sheet_versions.crew_id', 'call_sheet_versions.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'member_id'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
    )
    crew_id: Mapped[UUID]
    call_sheet_version_id: Mapped[UUID] = mapped_column(primary_key=True)
    member_id: Mapped[UUID] = mapped_column(primary_key=True)
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str] = mapped_column(String(30))
    required_at_publish: Mapped[bool] = mapped_column(Boolean)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CallSheetReview(Base):
    __tablename__ = 'call_sheet_reviews'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'call_sheet_version_id'], ['call_sheet_versions.crew_id', 'call_sheet_versions.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'reviewed_by'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
        CheckConstraint("decision IN ('accept','reject')", name='decision_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    call_sheet_version_id: Mapped[UUID]
    reviewed_by: Mapped[UUID]
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str] = mapped_column(String(10))
    review_note: Mapped[str | None] = mapped_column(String(500))
    reviewed_lock_version: Mapped[int] = mapped_column(Integer)
    content_snapshot: Mapped[dict] = mapped_column(JSONB)


class CallSheetReceipt(Base):
    __tablename__ = 'call_sheet_action_receipts'
    __table_args__ = (
        UniqueConstraint('crew_id', 'user_id', 'key_digest', name='uq_call_sheet_receipt'),
        ForeignKeyConstraint(['crew_id', 'member_id'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    user_id: Mapped[UUID]
    member_id: Mapped[UUID]
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(JSONB)


class CallSheetEvent(Base):
    __tablename__ = 'call_sheet_events'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'call_sheet_version_id'], ['call_sheet_versions.crew_id', 'call_sheet_versions.id'], ondelete='RESTRICT'),
        UniqueConstraint('call_sheet_version_id', 'object_lock_version', name='uq_call_sheet_event_version'),
        UniqueConstraint('crew_id', 'id', name='uq_call_sheet_event_scope'),
        Index('ix_call_sheet_events_pending', 'created_at', 'id', postgresql_where=text('consumed_at IS NULL')),
        CheckConstraint("kind IN ('published','review_required','withdrawn','superseded')", name='kind_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    call_sheet_version_id: Mapped[UUID]
    object_lock_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    recipient_snapshots: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
