from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.infrastructure.database import Base


class Crew(Base):
    __tablename__ = "crews"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 50 AND name = btrim(name) AND name !~ '^[[:space:]]*$' AND name !~ '[[:cntrl:]]'", name="name_valid"),
        CheckConstraint("char_length(project_name) BETWEEN 1 AND 100 AND project_name = btrim(project_name) AND project_name !~ '^[[:space:]]*$' AND project_name !~ '[[:cntrl:]]'", name="project_name_valid"),
        CheckConstraint("description IS NULL OR char_length(description) BETWEEN 1 AND 1000", name="description_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(50))
    project_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CrewMember(Base):
    __tablename__ = "crew_members"
    __table_args__ = (
        UniqueConstraint("crew_id", "user_id", name="uq_crew_members_crew_user"),
        UniqueConstraint("crew_id", "id", name="uq_crew_members_crew_id_id"),
        CheckConstraint("status IN ('active', 'left', 'removed')", name="status_valid"),
        CheckConstraint("(status = 'active' AND ended_at IS NULL) OR (status <> 'active' AND ended_at IS NOT NULL)", name="ended_state_pair"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 30 AND display_name = btrim(display_name) AND display_name !~ '^[[:space:]]*$' AND display_name !~ '[[:cntrl:]]'", name="display_name_valid"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID] = mapped_column(ForeignKey("crews.id", ondelete="RESTRICT"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    display_name: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Role(Base):
    __tablename__ = "roles"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(30))


class Permission(Base):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str] = mapped_column(String(500))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code", ondelete="RESTRICT"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permissions.code", ondelete="RESTRICT"), primary_key=True)


class MemberRole(Base):
    __tablename__ = "member_roles"
    __table_args__ = (ForeignKeyConstraint(["crew_id", "member_id"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),)
    crew_id: Mapped[UUID] = mapped_column(primary_key=True)
    member_id: Mapped[UUID] = mapped_column(primary_key=True)
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code", ondelete="RESTRICT"), primary_key=True)


class MemberPermissionOverride(Base):
    __tablename__ = "member_permission_overrides"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "member_id"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        CheckConstraint("effect IN ('allow', 'deny')", name="effect_valid"),
        CheckConstraint("permission_code NOT IN ('member.invite', 'member.remove', 'member.authorize')", name="organization_override_forbidden"),
    )
    crew_id: Mapped[UUID] = mapped_column(primary_key=True)
    member_id: Mapped[UUID] = mapped_column(primary_key=True)
    permission_code: Mapped[str] = mapped_column(ForeignKey("permissions.code", ondelete="RESTRICT"), primary_key=True)
    effect: Mapped[str] = mapped_column(String(5))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("actor_type IN ('user', 'system')", name="actor_type_valid"),
        CheckConstraint("result IN ('success', 'denied', 'failed')", name="result_valid"),
        CheckConstraint("actor_type <> 'user' OR actor_id IS NOT NULL", name="user_actor_required"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID | None] = mapped_column(ForeignKey("crews.id", ondelete="RESTRICT"), index=True)
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[UUID | None] = mapped_column()
    before_summary: Mapped[dict | None] = mapped_column(JSONB)
    after_summary: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(100))
    request_id: Mapped[str | None] = mapped_column(String(32))
    run_id: Mapped[UUID | None] = mapped_column()
    proposal_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CrewInvitation(Base):
    __tablename__ = "crew_invitations"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "invited_by_member_id"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        CheckConstraint("status IN ('pending','accepted','revoked','expired')", name="status_valid"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        CheckConstraint("char_length(email_normalized) BETWEEN 1 AND 254", name="email_valid"),
        CheckConstraint("token_digest ~ '^[a-f0-9]{64}$'", name="token_digest_valid"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("(status = 'accepted' AND accepted_by_user_id IS NOT NULL AND accepted_at IS NOT NULL) OR (status <> 'accepted' AND accepted_by_user_id IS NULL AND accepted_at IS NULL)", name="acceptance_pair"),
        Index("uq_crew_invitations_pending_email", "crew_id", "email_normalized", unique=True, postgresql_where=text("status = 'pending'")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID] = mapped_column(ForeignKey("crews.id", ondelete="RESTRICT"))
    email_normalized: Mapped[str] = mapped_column(String(254))
    token_digest: Mapped[str] = mapped_column(Text, unique=True)
    invited_by_member_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
