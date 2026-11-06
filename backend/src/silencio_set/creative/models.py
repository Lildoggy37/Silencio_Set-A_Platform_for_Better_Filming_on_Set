from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from silencio_set.infrastructure.database import Base


class Breakdown(Base):
    __tablename__ = "scene_breakdowns"
    __table_args__ = (
        UniqueConstraint("crew_id", "id", name="uq_breakdown_scope"),
        UniqueConstraint("crew_id", "id", "script_version_id", name="uq_breakdown_source"),
        UniqueConstraint("script_version_id", "version_no", name="uq_breakdown_number"),
        ForeignKeyConstraint(["crew_id", "script_version_id"], ["script_versions.crew_id", "script_versions.id"], ondelete="RESTRICT"),
        *(ForeignKeyConstraint(["crew_id", f], ["crew_members.crew_id", "crew_members.id"], name=f"fk_breakdown_{f}", ondelete="RESTRICT") for f in ("created_by", "reviewed_by")),
        CheckConstraint("review_status IN ('draft','pending_review','confirmed','rejected')", name="status_valid"),
        CheckConstraint("lock_version BETWEEN 1 AND 2147483647 AND version_no > 0", name="version_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    script_version_id: Mapped[UUID]
    version_no: Mapped[int] = mapped_column(Integer)
    review_status: Mapped[str] = mapped_column(String(20))
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[UUID]
    reviewed_by: Mapped[UUID | None]
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (UniqueConstraint("crew_id", "id", name="uq_scene_scope"),
        ForeignKeyConstraint(["crew_id", "script_id"], ["scripts.crew_id", "scripts.id"], ondelete="RESTRICT"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    script_id: Mapped[UUID]


class SceneRevision(Base):
    __tablename__ = "scene_revisions"
    __table_args__ = (
        UniqueConstraint("crew_id", "id", name="uq_scene_revision_scope"),
        UniqueConstraint("breakdown_id", "scene_number", name="uq_breakdown_scene_number"),
        UniqueConstraint("breakdown_id", "scene_id", name="uq_breakdown_scene_identity"),
        ForeignKeyConstraint(["crew_id", "breakdown_id", "script_version_id"], ["scene_breakdowns.crew_id", "scene_breakdowns.id", "scene_breakdowns.script_version_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "scene_id"], ["scenes.crew_id", "scenes.id"], ondelete="RESTRICT"),
        CheckConstraint("source_start > 0 AND source_end >= source_start", name="source_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    scene_id: Mapped[UUID]
    breakdown_id: Mapped[UUID]
    script_version_id: Mapped[UUID]
    scene_number: Mapped[str] = mapped_column(String(20))
    source_start: Mapped[int] = mapped_column(Integer)
    source_end: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSONB)


class StoryboardVersion(Base):
    __tablename__ = "storyboard_versions"
    __table_args__ = (
        UniqueConstraint("crew_id", "id", name="uq_storyboard_scope"),
        UniqueConstraint("scene_revision_id", "version_no", name="uq_storyboard_number"),
        ForeignKeyConstraint(["crew_id", "scene_revision_id"], ["scene_revisions.crew_id", "scene_revisions.id"], ondelete="RESTRICT"),
        *(ForeignKeyConstraint(["crew_id", f], ["crew_members.crew_id", "crew_members.id"], name=f"fk_storyboard_{f}", ondelete="RESTRICT") for f in ("created_by", "reviewed_by")),
        CheckConstraint("review_status IN ('draft','pending_review','confirmed','rejected')", name="status_valid"),
        CheckConstraint("lock_version BETWEEN 1 AND 2147483647 AND version_no > 0", name="version_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    scene_revision_id: Mapped[UUID]
    version_no: Mapped[int] = mapped_column(Integer)
    review_status: Mapped[str] = mapped_column(String(20))
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    shots: Mapped[list] = mapped_column(JSONB)
    created_by: Mapped[UUID]
    reviewed_by: Mapped[UUID | None]
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SceneCharacter(Base):
    __tablename__ = "scene_characters"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "scene_revision_id"], ["scene_revisions.crew_id", "scene_revisions.id"], ondelete="CASCADE"),
        UniqueConstraint("scene_revision_id", "name", name="uq_scene_character_name"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    scene_revision_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(100))


class CharacterCasting(Base):
    __tablename__ = 'character_castings'
    __table_args__ = (
        UniqueConstraint('script_id', 'character_name', name='uq_casting_character'),
        ForeignKeyConstraint(['crew_id', 'script_id'], ['scripts.crew_id', 'scripts.id'], ondelete='RESTRICT'),
        *(ForeignKeyConstraint(['crew_id', field], ['crew_members.crew_id', 'crew_members.id'], name=f'fk_casting_{field}', ondelete='RESTRICT') for field in ('member_id', 'updated_by')),
        CheckConstraint('char_length(character_name) BETWEEN 1 AND 100', name='name_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    script_id: Mapped[UUID]
    character_name: Mapped[str] = mapped_column(String(100))
    member_id: Mapped[UUID]
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str] = mapped_column(String(30))
    updated_by: Mapped[UUID]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SceneResourceNeed(Base):
    __tablename__ = "scene_resource_needs"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "scene_revision_id"], ["scene_revisions.crew_id", "scene_revisions.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["crew_id", "resource_id"], ["resources.crew_id", "resources.id"], ondelete="RESTRICT"),
        UniqueConstraint("scene_revision_id", "resource_type", "name", name="uq_scene_need_name"),
        UniqueConstraint("scene_revision_id", "resource_id", name="uq_scene_need_resource"),
        CheckConstraint("resource_type IN ('equipment','prop','location','consumable')", name="type_valid"),
        CheckConstraint("quantity BETWEEN 1 AND 1000000", name="quantity_valid"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100 AND char_length(unit) BETWEEN 1 AND 10", name="text_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    scene_revision_id: Mapped[UUID]
    resource_type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[int] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(10))
    resource_id: Mapped[UUID | None]


class CreativeReview(Base):
    __tablename__ = "creative_reviews"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "breakdown_id"], ["scene_breakdowns.crew_id", "scene_breakdowns.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "storyboard_version_id"], ["storyboard_versions.crew_id", "storyboard_versions.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "reviewed_by"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        CheckConstraint("(breakdown_id IS NULL) <> (storyboard_version_id IS NULL)", name="one_target"),
        CheckConstraint("decision IN ('accept','reject')", name="decision_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    breakdown_id: Mapped[UUID | None]
    storyboard_version_id: Mapped[UUID | None]
    reviewed_by: Mapped[UUID]
    decision: Mapped[str] = mapped_column(String(10))
    review_note: Mapped[str | None] = mapped_column(String(500))
    reviewed_lock_version: Mapped[int] = mapped_column(Integer)
    content_snapshot: Mapped[dict] = mapped_column(JSONB)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceReviewRecord(Base):
    __tablename__ = 'source_review_records'
    __table_args__ = (
        ForeignKeyConstraint(['crew_id', 'breakdown_id'], ['scene_breakdowns.crew_id', 'scene_breakdowns.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'storyboard_version_id'], ['storyboard_versions.crew_id', 'storyboard_versions.id'], ondelete='RESTRICT'),
        *(ForeignKeyConstraint(['crew_id', 'script_id', field], ['script_versions.crew_id', 'script_versions.script_id', 'script_versions.id'], name=f'fk_source_review_{field}', ondelete='RESTRICT') for field in ('source_script_version_id', 'working_script_version_id')),
        ForeignKeyConstraint(['crew_id', 'reviewed_by'], ['crew_members.crew_id', 'crew_members.id'], name='fk_source_review_member', ondelete='RESTRICT'),
        CheckConstraint('(breakdown_id IS NULL) <> (storyboard_version_id IS NULL)', name='one_target'),
        CheckConstraint("conclusion IN ('still_applicable','needs_revision') AND char_length(review_note) BETWEEN 1 AND 1000", name='conclusion_valid'),
        CheckConstraint('target_lock_version > 0 AND working_revision > 0 AND source_script_version_id <> working_script_version_id', name='basis_valid'),
        Index('ix_source_reviews_breakdown', 'crew_id', 'breakdown_id', 'working_revision', 'target_lock_version', 'reviewed_at', 'id'),
        Index('ix_source_reviews_storyboard', 'crew_id', 'storyboard_version_id', 'working_revision', 'target_lock_version', 'reviewed_at', 'id'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    breakdown_id: Mapped[UUID | None]
    storyboard_version_id: Mapped[UUID | None]
    script_id: Mapped[UUID]
    source_script_version_id: Mapped[UUID]
    working_script_version_id: Mapped[UUID]
    working_revision: Mapped[int] = mapped_column(Integer)
    target_lock_version: Mapped[int] = mapped_column(Integer)
    reviewed_by: Mapped[UUID]
    reviewer_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    conclusion: Mapped[str] = mapped_column(String(20))
    review_note: Mapped[str] = mapped_column(String(1000))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CreativeReceipt(Base):
    __tablename__ = "creative_action_receipts"
    __table_args__ = (UniqueConstraint("crew_id", "user_id", "key_digest", name="uq_creative_receipt_key"),
        ForeignKeyConstraint(["crew_id", "member_id"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    user_id: Mapped[UUID]
    member_id: Mapped[UUID]
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(JSONB)
