"""Account and registration receipt persistence. No accounts are seeded."""
from alembic import op
import sqlalchemy as sa

revision = "0002_accounts"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("email_normalized", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(30), nullable=True),
        sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email_normalized", name=op.f("uq_users_email_normalized")),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_users_status_valid")),
        sa.CheckConstraint("lock_version >= 1", name=op.f("ck_users_lock_version_positive")),
        sa.CheckConstraint("char_length(email) BETWEEN 1 AND 254", name=op.f("ck_users_email_not_empty")),
        sa.CheckConstraint("char_length(email_normalized) BETWEEN 1 AND 254", name=op.f("ck_users_email_normalized_not_empty")),
        sa.CheckConstraint("char_length(password_hash) > 0", name=op.f("ck_users_password_hash_not_empty")),
        sa.CheckConstraint(
            "display_name IS NULL OR (char_length(display_name) BETWEEN 1 AND 30 "
            "AND display_name = btrim(display_name) AND display_name !~ '^[[:space:]]*$' "
            "AND display_name !~ '[[:cntrl:]]')",
            name=op.f("ck_users_display_name_valid"),
        ),
        sa.CheckConstraint(
            "(display_name IS NULL AND profile_completed_at IS NULL) OR "
            "(display_name IS NOT NULL AND profile_completed_at IS NOT NULL)",
            name=op.f("ck_users_profile_completion_pair"),
        ),
    )
    op.create_table(
        "registration_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.Text(), nullable=False),
        sa.Column("email_normalized", sa.String(254), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registration_receipts")),
        sa.UniqueConstraint("token_digest", name=op.f("uq_registration_receipts_token_digest")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name=op.f("fk_registration_receipts_user_id_users")),
        sa.CheckConstraint("char_length(token_digest) > 0", name=op.f("ck_registration_receipts_token_digest_not_empty")),
        sa.CheckConstraint("char_length(email_normalized) BETWEEN 1 AND 254", name=op.f("ck_registration_receipts_email_normalized_not_empty")),
        sa.CheckConstraint("token_expires_at > consumed_at", name=op.f("ck_registration_receipts_consumed_before_expiry")),
    )
    op.create_index("ix_registration_receipts_user_id", "registration_receipts", ["user_id"])


def downgrade():
    op.drop_index("ix_registration_receipts_user_id", table_name="registration_receipts")
    op.drop_table("registration_receipts")
    op.drop_table("users")
