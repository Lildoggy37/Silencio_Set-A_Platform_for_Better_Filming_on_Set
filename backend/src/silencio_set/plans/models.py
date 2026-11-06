from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class ShootingPlan(Base):
    __tablename__ = 'shooting_plans'
    __table_args__ = (
        UniqueConstraint('crew_id', 'id', name='uq_plan_scope'),
        ForeignKeyConstraint(['crew_id', 'created_by'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'id', 'current_published_version_id'], ['shooting_plan_versions.crew_id', 'shooting_plan_versions.plan_id', 'shooting_plan_versions.id'], name='fk_plan_current_publication', use_alter=True, ondelete='RESTRICT'),
        CheckConstraint('char_length(name) BETWEEN 1 AND 100', name='name_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(100))
    current_published_version_id: Mapped[UUID | None]
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanVersion(Base):
    __tablename__ = 'shooting_plan_versions'
    __table_args__ = (
        UniqueConstraint('crew_id', 'id', name='uq_plan_version_scope'),
        UniqueConstraint('crew_id', 'plan_id', 'id', name='uq_plan_version_parent'),
        UniqueConstraint('plan_id', 'version_no', name='uq_plan_version_number'),
        ForeignKeyConstraint(['crew_id', 'plan_id'], ['shooting_plans.crew_id', 'shooting_plans.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'source_version_id'], ['shooting_plan_versions.crew_id', 'shooting_plan_versions.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'created_by'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'published_by'], ['crew_members.crew_id', 'crew_members.id'], name='fk_plan_publisher_member', ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'withdrawn_by'], ['crew_members.crew_id', 'crew_members.id'], name='fk_plan_withdrawer_member', ondelete='RESTRICT'),
        CheckConstraint("(withdrawn_at IS NULL AND withdrawn_by IS NULL AND withdrawal_reason IS NULL) OR (status = 'published' AND withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL AND withdrawal_reason IS NOT NULL AND char_length(withdrawal_reason) BETWEEN 1 AND 500)", name='withdrawal_valid'),
        CheckConstraint("status IN ('draft','published')", name='status_valid'),
        CheckConstraint("(status = 'draft' AND published_by IS NULL AND published_at IS NULL AND publication_snapshot IS NULL) OR (status = 'published' AND published_by IS NOT NULL AND published_at IS NOT NULL AND publication_snapshot IS NOT NULL)", name='publication_valid'),
        CheckConstraint('version_no > 0 AND lock_version BETWEEN 1 AND 2147483647', name='version_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    plan_id: Mapped[UUID]
    source_version_id: Mapped[UUID | None]
    version_no: Mapped[int] = mapped_column(Integer)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default='draft')
    published_by: Mapped[UUID | None]
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_by: Mapped[UUID | None]
    withdrawal_reason: Mapped[str | None] = mapped_column(String(500))
    publication_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    review_reasons: Mapped[list] = mapped_column(JSONB, default=list, server_default='[]')
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ShootingSession(Base):
    __tablename__ = 'shooting_sessions'
    __table_args__ = (
        UniqueConstraint('crew_id', 'id', name='uq_shooting_session_scope'),
        UniqueConstraint('plan_version_id', 'sequence_no', name='uq_shooting_session_sequence'),
        ForeignKeyConstraint(['crew_id', 'plan_version_id'], ['shooting_plan_versions.crew_id', 'shooting_plan_versions.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'scene_revision_id'], ['scene_revisions.crew_id', 'scene_revisions.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'location_resource_id'], ['resources.crew_id', 'resources.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'location_reservation_id'], ['resource_reservations.crew_id', 'resource_reservations.id'], ondelete='RESTRICT'),
        CheckConstraint('ends_at > starts_at', name='positive_interval'),
        CheckConstraint('sequence_no > 0 AND transition_minutes BETWEEN 0 AND 1440', name='sequence_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    plan_version_id: Mapped[UUID]
    scene_revision_id: Mapped[UUID]
    sequence_no: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    transition_minutes: Mapped[int] = mapped_column(Integer)
    location_resource_id: Mapped[UUID | None]
    location_reservation_id: Mapped[UUID | None]
    note: Mapped[str | None] = mapped_column(String(2000))


class SessionMember(Base):
    __tablename__ = 'session_members'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'session_id'], ['shooting_sessions.crew_id', 'shooting_sessions.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['crew_id', 'member_id'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
    )
    crew_id: Mapped[UUID]
    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    member_id: Mapped[UUID] = mapped_column(primary_key=True)
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionResource(Base):
    __tablename__ = 'session_resource_needs'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'session_id'], ['shooting_sessions.crew_id', 'shooting_sessions.id'], ondelete='CASCADE'),
        ForeignKeyConstraint(['crew_id', 'resource_id'], ['resources.crew_id', 'resources.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'reservation_id'], ['resource_reservations.crew_id', 'resource_reservations.id'], ondelete='RESTRICT'),
        CheckConstraint('quantity BETWEEN 1 AND 1000000', name='quantity_valid'),
    )
    crew_id: Mapped[UUID]
    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    resource_id: Mapped[UUID] = mapped_column(primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer)
    reservation_id: Mapped[UUID | None]


class PlanReceipt(Base):
    __tablename__ = 'plan_action_receipts'
    __table_args__ = (
        UniqueConstraint('crew_id', 'user_id', 'key_digest', name='uq_plan_receipt_key'),
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


class PlanPreflight(Base):
    __tablename__ = 'plan_preflight_checks'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'plan_version_id'], ['shooting_plan_versions.crew_id', 'shooting_plan_versions.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'checked_by'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
        UniqueConstraint('token_digest', name='uq_plan_check_token'),
        CheckConstraint('expires_at > created_at', name='expiry_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    plan_version_id: Mapped[UUID]
    checked_by: Mapped[UUID]
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_lock_version: Mapped[int] = mapped_column(Integer)
    expected_current_published_version_id: Mapped[UUID | None]
    token_digest: Mapped[str] = mapped_column(String(64))
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    data_snapshot: Mapped[dict] = mapped_column(JSONB)
    unknown_items: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanPublishConfirmation(Base):
    __tablename__ = 'plan_publish_confirmations'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'plan_version_id'], ['shooting_plan_versions.crew_id', 'shooting_plan_versions.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'confirmed_by'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    plan_version_id: Mapped[UUID]
    confirmed_by: Mapped[UUID]
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_data_revision: Mapped[str] = mapped_column(String(64))
    unknown_availability_snapshot: Mapped[list] = mapped_column(JSONB)


class PlanPublicationEvent(Base):
    __tablename__ = 'plan_publication_events'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'plan_version_id'], ['shooting_plan_versions.crew_id', 'shooting_plan_versions.id'], ondelete='RESTRICT'),
        UniqueConstraint('plan_version_id', 'kind', name='uq_plan_event_kind'),
        CheckConstraint("kind IN ('published','withdrawn')", name='kind_valid'),
        UniqueConstraint('crew_id', 'id', name='uq_plan_publication_event_scope'),
        Index('ix_plan_events_pending', 'created_at', 'id', postgresql_where=text('consumed_at IS NULL')),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    plan_version_id: Mapped[UUID]
    previous_version_id: Mapped[UUID | None]
    kind: Mapped[str] = mapped_column(String(20), default='published', server_default='published')
    recipient_snapshots: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
