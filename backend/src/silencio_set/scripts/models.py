from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from silencio_set.infrastructure.database import Base


class Script(Base):
    __tablename__ = "scripts"
    __table_args__ = (
        UniqueConstraint("crew_id", "id", name="uq_scripts_crew_id"),
        ForeignKeyConstraint(["crew_id", "created_by"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "id", "current_version_id"], ["script_versions.crew_id", "script_versions.script_id", "script_versions.id"], name="fk_script_current_version", use_alter=True, ondelete="RESTRICT"),
        CheckConstraint("lock_version BETWEEN 1 AND 2147483647", name="version_valid"),
        CheckConstraint("working_revision BETWEEN 0 AND 2147483647 AND ((current_version_id IS NULL AND working_revision = 0) OR (current_version_id IS NOT NULL AND working_revision > 0))", name='working_revision_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID] = mapped_column(ForeignKey("crews.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(100))
    current_version_id: Mapped[UUID | None]
    working_revision: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    created_by: Mapped[UUID]
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScriptSourceFile(Base):
    __tablename__ = 'script_source_files'
    __table_args__ = (
        UniqueConstraint('crew_id', 'script_id', 'id', name='uq_script_file_scope'),
        ForeignKeyConstraint(['crew_id', 'script_id'], ['scripts.crew_id', 'scripts.id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['crew_id', 'created_by'], ['crew_members.crew_id', 'crew_members.id'], ondelete='RESTRICT'),
        CheckConstraint("file_format IN ('docx','pdf','txt') AND byte_size BETWEEN 1 AND 20971520", name='file_valid'),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    crew_id: Mapped[UUID]
    script_id: Mapped[UUID]
    created_by: Mapped[UUID]
    original_name: Mapped[str] = mapped_column(String(200))
    file_format: Mapped[str] = mapped_column(String(10))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extraction_notes: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScriptVersion(Base):
    __tablename__ = "script_versions"
    __table_args__ = (
        UniqueConstraint("crew_id", "script_id", "id", name="uq_script_versions_scope"),
        UniqueConstraint("crew_id", "id", name="uq_script_versions_crew_id"),
        UniqueConstraint("script_id", "version_no", name="uq_script_versions_number"),
        ForeignKeyConstraint(["crew_id", "script_id"], ["scripts.crew_id", "scripts.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(['crew_id', 'script_id', 'source_file_id'], ['script_source_files.crew_id', 'script_source_files.script_id', 'script_source_files.id'], ondelete='RESTRICT', name='fk_script_version_source_file'),
        *(ForeignKeyConstraint(["crew_id", field], ["crew_members.crew_id", "crew_members.id"], name=f"fk_script_versions_{field}", ondelete="RESTRICT") for field in ("created_by", "confirmed_by")),
        CheckConstraint("status IN ('pending_check','confirmed')", name="status_valid"),
        CheckConstraint("(status = 'confirmed' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL) OR (status = 'pending_check' AND confirmed_by IS NULL AND confirmed_at IS NULL)", name="confirmation_pair"),
        CheckConstraint("version_no > 0 AND lock_version BETWEEN 1 AND 2147483647", name="version_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    script_id: Mapped[UUID]
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    confirmed_by: Mapped[UUID | None]
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    source_file_id: Mapped[UUID | None]


class ScriptParagraph(Base):
    __tablename__ = "script_paragraphs"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "script_version_id"], ["script_versions.crew_id", "script_versions.id"], ondelete="RESTRICT"),
        UniqueConstraint("script_version_id", "paragraph_no", name="uq_script_paragraph_number"),
        CheckConstraint("paragraph_no > 0 AND char_length(text) > 0", name="paragraph_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    script_version_id: Mapped[UUID]
    paragraph_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    source_page: Mapped[int | None] = mapped_column(Integer)


class ScriptReceipt(Base):
    __tablename__ = "script_action_receipts"
    __table_args__ = (
        UniqueConstraint("crew_id", "user_id", "key_digest", name="uq_script_receipts_key"),
        ForeignKeyConstraint(["crew_id", "script_id"], ["scripts.crew_id", "scripts.id"], ondelete="RESTRICT"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    membership_joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    script_id: Mapped[UUID]
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(JSONB)
