"""Private conversation history; checkpoints are stored separately by LangGraph."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class Conversation(Base):
    __tablename__ = 'diane_conversations'
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID] = mapped_column(ForeignKey('crews.id', ondelete='RESTRICT'), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    member_id: Mapped[UUID] = mapped_column(ForeignKey('crew_members.id', ondelete='RESTRICT'))
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(80))
    next_ordinal: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationTurn(Base):
    __tablename__ = 'diane_conversation_turns'
    __table_args__ = (
        UniqueConstraint('conversation_id', 'ordinal'),
        CheckConstraint("status IN ('queued','running','completed','failed','interrupted')", name='status_valid'),
        CheckConstraint('ordinal >= 1', name='ordinal_positive'),
        CheckConstraint('progress_version >= 1', name='progress_version_positive'),
    )
    # The client-generated ID is also the idempotency key and checkpoint thread ID.
    id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('diane_conversations.id', ondelete='CASCADE'), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    phase: Mapped[str] = mapped_column(String(30), default='model')
    progress_version: Mapped[int] = mapped_column(Integer, default=1)
    request_id: Mapped[str | None] = mapped_column(String(32))
    dispatch_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_token: Mapped[UUID | None] = mapped_column()
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Preference(Base):
    __tablename__ = 'diane_preferences'
    __table_args__ = (
        UniqueConstraint('crew_id', 'user_id', 'member_id', 'membership_joined_at'),
        CheckConstraint("response_style IN ('concise','detailed')", name='style_valid'),
        CheckConstraint("focus IN ('general','schedule','resources')", name='focus_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID] = mapped_column(ForeignKey('crews.id', ondelete='RESTRICT'))
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    member_id: Mapped[UUID] = mapped_column(ForeignKey('crew_members.id', ondelete='RESTRICT'))
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_style: Mapped[str] = mapped_column(String(20), default='concise')
    focus: Mapped[str] = mapped_column(String(20), default='general')
