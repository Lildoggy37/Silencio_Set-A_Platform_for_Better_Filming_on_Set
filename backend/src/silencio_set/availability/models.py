from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class AvailabilitySet(Base):
    __tablename__ = "member_availability_sets"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "member_id"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        CheckConstraint("lock_version BETWEEN 1 AND 2147483647", name="version_valid"),
    )
    crew_id: Mapped[UUID] = mapped_column(primary_key=True)
    member_id: Mapped[UUID] = mapped_column(primary_key=True)
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemberAvailability(Base):
    __tablename__ = "member_availability"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "member_id"], ["member_availability_sets.crew_id", "member_availability_sets.member_id"], ondelete="RESTRICT"),
        CheckConstraint("ends_at > starts_at", name="positive_interval"),
        CheckConstraint("availability IN ('available','unavailable')", name="availability_valid"),
        CheckConstraint("lock_version BETWEEN 1 AND 2147483647", name="version_valid"),
        ExcludeConstraint(("crew_id", "="), ("member_id", "="),
            (text("tstzrange(starts_at, ends_at, '[)')"), "&&"), name="ex_member_availability_overlap", using="gist"),
        Index("ix_member_availability_scope_start", "crew_id", "member_id", "starts_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    member_id: Mapped[UUID]
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    availability: Mapped[str] = mapped_column(String(20))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
