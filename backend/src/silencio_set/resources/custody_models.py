"""Physical custody ledger; expiry never removes outstanding property."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class Checkout(Base):
    __tablename__ = "resource_checkouts"
    __table_args__ = (
        UniqueConstraint("crew_id", "id", name="uq_checkouts_crew_id"),
        ForeignKeyConstraint(["crew_id", "resource_id"], ["resources.crew_id", "resources.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "reservation_id"], ["resource_reservations.crew_id", "resource_reservations.id"], ondelete="RESTRICT"),
        *(ForeignKeyConstraint(["crew_id", field], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT", name=f"fk_checkouts_{field}") for field in ("borrower_member_id", "handled_by")),
        CheckConstraint("quantity BETWEEN 1 AND 1000000 AND returned_quantity BETWEEN 0 AND quantity", name="quantity_valid"),
        CheckConstraint("expected_return_at >= checked_out_at", name="return_time_valid"),
        CheckConstraint("lock_version BETWEEN 1 AND 2147483647", name="version_valid"),
        Index("ix_checkouts_resource", "crew_id", "resource_id"),
        Index("ix_checkouts_reservation", "crew_id", "reservation_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    resource_id: Mapped[UUID]
    reservation_id: Mapped[UUID]
    borrower_member_id: Mapped[UUID]
    borrower_membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quantity: Mapped[int] = mapped_column(Integer)
    returned_quantity: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    checked_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expected_return_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    handled_by: Mapped[UUID]
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))


class ResourceReturn(Base):
    __tablename__ = "resource_returns"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "checkout_id"], ["resource_checkouts.crew_id", "resource_checkouts.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "handled_by"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        CheckConstraint("quantity BETWEEN 1 AND 1000000", name="quantity_valid"),
        CheckConstraint("note IS NULL OR char_length(note) BETWEEN 1 AND 500", name="note_valid"),
        Index("ix_returns_checkout", "crew_id", "checkout_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    checkout_id: Mapped[UUID]
    quantity: Mapped[int] = mapped_column(Integer)
    returned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handled_by: Mapped[UUID]
    note: Mapped[str | None] = mapped_column(String(500))
