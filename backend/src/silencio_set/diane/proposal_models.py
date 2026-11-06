from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class TaskProposal(Base):
    __tablename__ = 'diane_task_proposals'
    __table_args__ = (
        CheckConstraint("status IN ('pending','completed','rejected')", name='status_valid'),
        CheckConstraint('lock_version >= 1', name='version_positive'),
        CheckConstraint("jsonb_typeof(tasks) = 'array' AND jsonb_array_length(tasks) BETWEEN 1 AND 5", name='task_count'),
        CheckConstraint("(status = 'pending' AND decided_at IS NULL AND decision_version IS NULL) OR (status <> 'pending' AND decided_at IS NOT NULL AND decision_version IS NOT NULL)", name='decision_pair'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID] = mapped_column(ForeignKey('crews.id', ondelete='RESTRICT'), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    member_id: Mapped[UUID] = mapped_column(ForeignKey('crew_members.id', ondelete='RESTRICT'))
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default='pending')
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    tasks: Mapped[list] = mapped_column(JSONB)
    member_snapshot: Mapped[list] = mapped_column(JSONB)
    generation: Mapped[dict | None] = mapped_column(JSONB)
    created_task_ids: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_version: Mapped[int | None] = mapped_column(Integer)
