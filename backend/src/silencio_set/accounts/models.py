from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.accounts.identity import normalize_display_name
from silencio_set.infrastructure.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        CheckConstraint("char_length(email) BETWEEN 1 AND 254", name="email_not_empty"),
        CheckConstraint("char_length(email_normalized) BETWEEN 1 AND 254", name="email_normalized_not_empty"),
        CheckConstraint("char_length(password_hash) > 0", name="password_hash_not_empty"),
        CheckConstraint(
            "display_name IS NULL OR (char_length(display_name) BETWEEN 1 AND 30 "
            "AND display_name = btrim(display_name) AND display_name !~ '^[[:space:]]*$' "
            "AND display_name !~ '[[:cntrl:]]')",
            name="display_name_valid",
        ),
        CheckConstraint(
            "(display_name IS NULL AND profile_completed_at IS NULL) OR "
            "(display_name IS NOT NULL AND profile_completed_at IS NOT NULL)",
            name="profile_completion_pair",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(254))
    email_normalized: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(30))
    profile_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    lock_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __mapper_args__ = {"version_id_col": lock_version}

    @property
    def profile_completed(self) -> bool:
        if self.profile_completed_at is None or self.display_name is None:
            return False
        try:
            return normalize_display_name(self.display_name) == self.display_name
        except ValueError:
            return False

    @property
    def missing_fields(self) -> list[str]:
        return [] if self.profile_completed else ["display_name"]


class RegistrationReceipt(Base):
    __tablename__ = "registration_receipts"
    __table_args__ = (
        CheckConstraint("char_length(token_digest) > 0", name="token_digest_not_empty"),
        CheckConstraint("char_length(email_normalized) BETWEEN 1 AND 254", name="email_normalized_not_empty"),
        CheckConstraint("token_expires_at > consumed_at", name="consumed_before_expiry"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_digest: Mapped[str] = mapped_column(Text, unique=True)
    email_normalized: Mapped[str] = mapped_column(String(254))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
