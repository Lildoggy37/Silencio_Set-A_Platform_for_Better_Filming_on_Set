from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint("job_type", "idempotency_key", name="uq_background_jobs_type_key"),
        CheckConstraint("status IN ('pending_dispatch','queued','running','retry_wait','succeeded','failed','cancelled')", name="status_valid"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("status != 'running' OR (execution_token IS NOT NULL AND lease_expires_at IS NOT NULL)", name="running_has_lease"),
        Index("ix_background_jobs_status_lease", "status", "lease_expires_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID | None]
    job_type: Mapped[str] = mapped_column(String(50))
    payload_ref: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="pending_dispatch", server_default="pending_dispatch")
    idempotency_key: Mapped[str] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    execution_token: Mapped[UUID | None]
    worker_id: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result_ref: Mapped[dict | None] = mapped_column(JSONB)
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("status IN ('pending','dispatched','failed')", name="status_valid"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("(dispatch_token IS NULL) = (lease_expires_at IS NULL)", name="lease_pair"),
        Index("ix_outbox_events_pending", "status", "next_attempt_at", "lease_expires_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID | None]
    job_id: Mapped[UUID] = mapped_column(ForeignKey("background_jobs.id", ondelete="RESTRICT"), index=True)
    event_key: Mapped[str] = mapped_column(Text, unique=True)
    event_type: Mapped[str] = mapped_column(String(50))
    aggregate_ref: Mapped[dict] = mapped_column(JSONB)
    payload_ref: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    dispatch_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
