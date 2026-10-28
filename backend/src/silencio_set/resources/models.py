from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("crew_id", "id", name="uq_resources_crew_id_id"),
        *(ForeignKeyConstraint(["crew_id", field], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT", name=f"fk_resources_{field}_member") for field in ("manager_member_id", "created_by_member_id")),
        CheckConstraint("resource_type IN ('equipment','prop','location')", name="type_valid"),
        CheckConstraint("status IN ('available','disabled')", name="status_valid"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100 AND name = btrim(name) AND name !~ '[[:cntrl:]]'", name="name_valid"),
        CheckConstraint("char_length(unit) BETWEEN 1 AND 10 AND unit !~ '[[:cntrl:]]'", name="unit_valid"),
        CheckConstraint("total_quantity BETWEEN 1 AND 1000000", name="quantity_valid"),
        CheckConstraint("lock_version BETWEEN 1 AND 2147483647", name="version_valid"),
        Index("ix_resources_crew_created", "crew_id", "created_at", "id"),
        Index("ix_resources_crew_manager", "crew_id", "manager_member_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID] = mapped_column(ForeignKey("crews.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(20))
    manager_member_id: Mapped[UUID]
    created_by_member_id: Mapped[UUID]
    unit: Mapped[str] = mapped_column(String(10))
    total_quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="available", server_default="available")
    description: Mapped[str | None] = mapped_column(String(2000))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResourceScopeGrant(Base):
    __tablename__ = "resource_scope_grants"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "resource_id"], ["resources.crew_id", "resources.id"], ondelete="RESTRICT"),
        *(ForeignKeyConstraint(["crew_id", field], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT", name=f"fk_resource_scope_grants_{field}_member") for field in ("member_id", "granted_by")),
        CheckConstraint("permission_code IN ('resource.manage','reservation.review','resource.checkout','resource.return','stock.manage')", name="code_valid"),
    )
    crew_id: Mapped[UUID] = mapped_column(primary_key=True)
    resource_id: Mapped[UUID] = mapped_column(primary_key=True)
    member_id: Mapped[UUID] = mapped_column(primary_key=True)
    permission_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reservation(Base):
    __tablename__ = "resource_reservations"
    __table_args__ = (
        UniqueConstraint("crew_id", "id", name="uq_resource_reservations_crew_id_id"),
        ForeignKeyConstraint(["crew_id", "resource_id"], ["resources.crew_id", "resources.id"], ondelete="RESTRICT"),
        *(ForeignKeyConstraint(["crew_id", field], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT", name=f"fk_resource_reservations_{field}_member") for field in ("applicant_member_id", "reviewed_by", "cancelled_by")),
        CheckConstraint("ends_at > starts_at", name="positive_interval"),
        CheckConstraint("quantity BETWEEN 1 AND 1000000", name="quantity_valid"),
        CheckConstraint("status IN ('pending','confirmed','rejected','cancelled')", name="status_valid"),
        CheckConstraint("(reviewed_by IS NULL AND reviewed_at IS NULL) OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)", name="review_pair"),
        CheckConstraint("status NOT IN ('confirmed','rejected') OR reviewed_by IS NOT NULL", name="reviewed_state"),
        CheckConstraint("status <> 'rejected' OR (decision_note IS NOT NULL AND char_length(decision_note) BETWEEN 1 AND 500)", name="rejection_note"),
        CheckConstraint("(status = 'cancelled' AND cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL) OR (status <> 'cancelled' AND cancelled_by IS NULL AND cancelled_at IS NULL AND cancel_reason IS NULL)", name="cancel_pair"),
        CheckConstraint("lock_version BETWEEN 1 AND 2147483647", name="version_valid"),
        Index("ix_reservations_capacity", "crew_id", "resource_id", "status", "starts_at", "ends_at"),
        Index("ix_reservations_applicant", "crew_id", "applicant_member_id", "status", "ends_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    resource_id: Mapped[UUID]
    applicant_member_id: Mapped[UUID]
    applicant_membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    reviewed_by: Mapped[UUID | None]
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    approval_request_key: Mapped[str | None] = mapped_column(String(64))
    cancelled_by: Mapped[UUID | None]
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResourceReceipt(Base):
    __tablename__ = "resource_action_receipts"
    __table_args__ = (
        UniqueConstraint("crew_id", "user_id", "key_digest", name="uq_resource_receipts_actor_key"),
        ForeignKeyConstraint(["crew_id", "resource_id"], ["resources.crew_id", "resources.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "reservation_id"], ["resource_reservations.crew_id", "resource_reservations.id"], ondelete="RESTRICT"),
        CheckConstraint("key_digest ~ '^[a-f0-9]{64}$' AND request_digest ~ '^[a-f0-9]{64}$'", name="digests_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resource_id: Mapped[UUID]
    reservation_id: Mapped[UUID | None]
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResourceChangeEvent(Base):
    """Durable intent; resource notification delivery is a later integration."""
    __tablename__ = "resource_change_events"
    __table_args__ = (
        UniqueConstraint("crew_id", "event_key", name="uq_resource_events_key"),
        ForeignKeyConstraint(["crew_id", "resource_id"], ["resources.crew_id", "resources.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "reservation_id"], ["resource_reservations.crew_id", "resource_reservations.id"], ondelete="RESTRICT"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    resource_id: Mapped[UUID]
    reservation_id: Mapped[UUID | None]
    event_key: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(50))
    recipient_member_ids: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
