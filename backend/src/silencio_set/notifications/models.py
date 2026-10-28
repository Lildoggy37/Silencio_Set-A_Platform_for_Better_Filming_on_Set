from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("crew_id", "id", name="uq_notifications_crew_id_id"),
        UniqueConstraint("crew_id", "event_key", name="uq_notifications_crew_event"),
        UniqueConstraint("source_event_id", name="uq_notifications_source_event"),
        ForeignKeyConstraint(["crew_id", "source_event_id"], ["task_change_events.crew_id", "task_change_events.id"], ondelete="RESTRICT"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 100", name="title_valid"),
        CheckConstraint("char_length(summary) BETWEEN 1 AND 2000", name="summary_valid"),
        Index("ix_notifications_crew_created", "crew_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    source_event_id: Mapped[UUID]
    event_key: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationRecipient(Base):
    __tablename__ = "notification_recipients"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "notification_id"], ["notifications.crew_id", "notifications.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "member_id"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        CheckConstraint("confirmed_at IS NULL OR (requires_confirmation AND read_at IS NOT NULL AND confirmed_at >= read_at)", name="confirmation_pair"),
        Index("ix_notification_recipients_member_read", "crew_id", "member_id", "read_at", "notification_id"),
    )
    crew_id: Mapped[UUID] = mapped_column(primary_key=True)
    notification_id: Mapped[UUID] = mapped_column(primary_key=True)
    member_id: Mapped[UUID] = mapped_column(primary_key=True)
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
