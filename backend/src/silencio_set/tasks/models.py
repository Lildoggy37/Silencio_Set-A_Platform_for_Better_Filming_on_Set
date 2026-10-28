from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("crew_id", "id", name="uq_tasks_crew_id_id"),
        *(ForeignKeyConstraint(["crew_id", field], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT", name=f"fk_tasks_{field}_member")
          for field in ("owner_member_id", "reviewer_member_id", "created_by_member_id")),
        CheckConstraint("char_length(title) BETWEEN 1 AND 100 AND title = btrim(title) AND title !~ '[[:cntrl:]]'", name="title_valid"),
        CheckConstraint("description IS NULL OR char_length(description) BETWEEN 1 AND 3000", name="description_valid"),
        CheckConstraint("status IN ('pending','in_progress','pending_review','completed','cancelled')", name="status_valid"),
        CheckConstraint("(status = 'cancelled' AND cancel_reason IS NOT NULL AND char_length(cancel_reason) BETWEEN 1 AND 500) OR (status <> 'cancelled' AND cancel_reason IS NULL)", name="cancel_pair"),
        CheckConstraint("lock_version BETWEEN 1 AND 2147483647", name="version_valid"),
        Index("ix_tasks_crew_due", "crew_id", "due_at", "id"),
        Index("ix_tasks_crew_owner_status", "crew_id", "owner_member_id", "status"),
        Index("ix_tasks_crew_reviewer_status", "crew_id", "reviewer_member_id", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID] = mapped_column(ForeignKey("crews.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(3000))
    owner_member_id: Mapped[UUID]
    reviewer_member_id: Mapped[UUID]
    created_by_member_id: Mapped[UUID]
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TaskCollaborator(Base):
    __tablename__ = "task_collaborators"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "task_id"], ["tasks.crew_id", "tasks.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "member_id"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        Index("ix_task_collaborators_member", "crew_id", "member_id", "task_id"),
    )
    crew_id: Mapped[UUID] = mapped_column(primary_key=True)
    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    member_id: Mapped[UUID] = mapped_column(primary_key=True)


class TaskSubmission(Base):
    __tablename__ = "task_submissions"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "task_id"], ["tasks.crew_id", "tasks.id"], ondelete="RESTRICT"),
        *(ForeignKeyConstraint(["crew_id", field], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT", name=f"fk_task_submissions_{field}_member")
          for field in ("submitted_by", "reviewed_by")),
        UniqueConstraint("task_id", "submission_no", name="uq_task_submissions_number"),
        CheckConstraint("submission_no >= 1", name="number_positive"),
        CheckConstraint("char_length(completion_note) BETWEEN 1 AND 2000", name="note_valid"),
        CheckConstraint("review_status IN ('pending','accepted','rejected','cancelled')", name="review_status_valid"),
        CheckConstraint("(review_status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL) OR (review_status <> 'pending' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND reviewed_at >= submitted_at)", name="review_pair"),
        CheckConstraint("review_status <> 'rejected' OR (review_note IS NOT NULL AND char_length(review_note) BETWEEN 1 AND 500)", name="rejection_note"),
        Index("uq_task_submissions_current_pending", "task_id", unique=True, postgresql_where=text("review_status = 'pending'")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    task_id: Mapped[UUID]
    submission_no: Mapped[int] = mapped_column(Integer)
    submitted_by: Mapped[UUID]
    completion_note: Mapped[str] = mapped_column(String(2000))
    review_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    reviewed_by: Mapped[UUID | None]
    review_note: Mapped[str | None] = mapped_column(String(500))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskReceipt(Base):
    __tablename__ = "task_action_receipts"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "task_id"], ["tasks.crew_id", "tasks.id"], ondelete="RESTRICT"),
        UniqueConstraint("crew_id", "user_id", "key_digest", name="uq_task_receipts_actor_key"),
        CheckConstraint("key_digest ~ '^[a-f0-9]{64}$' AND request_digest ~ '^[a-f0-9]{64}$'", name="digests_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    task_id: Mapped[UUID]
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskChangeEvent(Base):
    """Durable notification intent consumed by the in-app notification poller."""
    __tablename__ = "task_change_events"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "task_id"], ["tasks.crew_id", "tasks.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "actor_member_id"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        UniqueConstraint("task_id", "task_version", name="uq_task_events_version"),
        UniqueConstraint("crew_id", "id", name="uq_task_events_crew_id_id"),
        CheckConstraint("dispatch_status IN ('pending','dispatched','failed')", name="dispatch_status_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    task_id: Mapped[UUID]
    task_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    actor_member_id: Mapped[UUID]
    recipient_member_ids: Mapped[list] = mapped_column(JSONB)
    dispatch_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
