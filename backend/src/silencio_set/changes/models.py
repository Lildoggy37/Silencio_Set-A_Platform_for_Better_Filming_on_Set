from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from silencio_set.infrastructure.database import Base


class ImpactCheck(Base):
    __tablename__ = 'impact_check_runs'
    __table_args__ = (
        UniqueConstraint('crew_id', 'id', name='uq_impact_check_scope'),
        UniqueConstraint('crew_id', 'user_id', 'key_digest', name='uq_impact_check_request'),
        ForeignKeyConstraint(['crew_id', 'checked_by'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
        CheckConstraint("category IN ('breakdowns','storyboards','plans','call_sheets')", name='category_valid'),
        CheckConstraint('detected_count >= 0 AND updated_count >= 0 AND resolved_count >= 0 AND reopened_count >= 0 AND open_count >= 0', name='counts_valid'),
        Index('ix_impact_check_category', 'crew_id', 'category', 'checked_at', 'id'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    user_id: Mapped[UUID]
    checked_by: Mapped[UUID]
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    category: Mapped[str] = mapped_column(String(20))
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detected_count: Mapped[int] = mapped_column(Integer)
    updated_count: Mapped[int] = mapped_column(Integer)
    resolved_count: Mapped[int] = mapped_column(Integer)
    reopened_count: Mapped[int] = mapped_column(Integer)
    open_count: Mapped[int] = mapped_column(Integer)


class ImpactIssue(Base):
    __tablename__ = 'impact_issues'
    __table_args__ = (
        UniqueConstraint('crew_id', 'id', name='uq_impact_issue_scope'),
        UniqueConstraint('crew_id', 'category', 'target_id', name='uq_impact_issue_target'),
        *(ForeignKeyConstraint(['crew_id', field], [f'{table}.crew_id', f'{table}.id'], ondelete='RESTRICT') for field, table in (
            ('breakdown_id','scene_breakdowns'), ('storyboard_version_id','storyboard_versions'),
            ('plan_version_id','shooting_plan_versions'), ('call_sheet_version_id','call_sheet_versions'))),
        ForeignKeyConstraint(['crew_id', 'last_check_id'], ['impact_check_runs.crew_id', 'impact_check_runs.id'], ondelete='RESTRICT'),
        CheckConstraint("(category = 'breakdowns' AND breakdown_id = target_id AND breakdown_id IS NOT NULL AND num_nonnulls(breakdown_id,storyboard_version_id,plan_version_id,call_sheet_version_id) = 1) OR (category = 'storyboards' AND storyboard_version_id = target_id AND storyboard_version_id IS NOT NULL AND num_nonnulls(breakdown_id,storyboard_version_id,plan_version_id,call_sheet_version_id) = 1) OR (category = 'plans' AND plan_version_id = target_id AND plan_version_id IS NOT NULL AND num_nonnulls(breakdown_id,storyboard_version_id,plan_version_id,call_sheet_version_id) = 1) OR (category = 'call_sheets' AND call_sheet_version_id = target_id AND call_sheet_version_id IS NOT NULL AND num_nonnulls(breakdown_id,storyboard_version_id,plan_version_id,call_sheet_version_id) = 1)", name='target_valid'),
        CheckConstraint("(status = 'open' AND resolved_at IS NULL AND resolution_basis IS NULL) OR (status = 'resolved' AND resolved_at IS NOT NULL AND resolution_basis IS NOT NULL)", name='resolution_valid'),
        CheckConstraint('lock_version BETWEEN 1 AND 2147483647 AND reopened_count >= 0 AND last_checked_at >= first_detected_at', name='version_valid'),
        Index('ix_impact_issue_status', 'crew_id', 'category', 'status', 'last_checked_at', 'id'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    category: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[UUID]
    breakdown_id: Mapped[UUID | None]
    storyboard_version_id: Mapped[UUID | None]
    plan_version_id: Mapped[UUID | None]
    call_sheet_version_id: Mapped[UUID | None]
    status: Mapped[str] = mapped_column(String(10))
    target_snapshot: Mapped[dict] = mapped_column(JSONB)
    evidence: Mapped[dict] = mapped_column(JSONB)
    # Open issues must satisfy resolution_basis IS NULL in PostgreSQL.
    resolution_basis: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_id: Mapped[UUID]
    reopened_count: Mapped[int] = mapped_column(Integer, default=0)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)


class ImpactTransition(Base):
    __tablename__ = 'impact_issue_transitions'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id','issue_id'], ['impact_issues.crew_id','impact_issues.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id','check_id'], ['impact_check_runs.crew_id','impact_check_runs.id'], ondelete='RESTRICT'),
        UniqueConstraint('issue_id','check_id', name='uq_impact_transition_check'),
        CheckConstraint("kind IN ('detected','updated','resolved','reopened')", name='kind_valid'),
        Index('ix_impact_transition_issue', 'crew_id', 'issue_id', 'recorded_at', 'id'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    issue_id: Mapped[UUID]
    check_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(12))
    before_state: Mapped[dict | None] = mapped_column(JSONB)
    after_state: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
