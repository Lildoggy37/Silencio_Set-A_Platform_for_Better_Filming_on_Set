"""Durable verification mail jobs and an expiring, leased outbox."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_mail_jobs"
down_revision = "0002_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("crew_id", sa.Uuid(), nullable=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("payload_ref", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(30), server_default="pending_dispatch", nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("execution_token", sa.Uuid(), nullable=True),
        sa.Column("worker_id", sa.String(200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_ref", postgresql.JSONB(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_jobs")),
        sa.UniqueConstraint("job_type", "idempotency_key", name="uq_background_jobs_type_key"),
        sa.CheckConstraint("status IN ('pending_dispatch','queued','running','retry_wait','succeeded','failed','cancelled')", name=op.f("ck_background_jobs_status_valid")),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_background_jobs_attempt_count_nonnegative")),
        sa.CheckConstraint("status != 'running' OR (execution_token IS NOT NULL AND lease_expires_at IS NOT NULL)", name=op.f("ck_background_jobs_running_has_lease")),
    )
    op.create_index("ix_background_jobs_status_lease", "background_jobs", ["status", "lease_expires_at"])
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("crew_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("aggregate_ref", postgresql.JSONB(), nullable=False),
        sa.Column("payload_ref", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dispatch_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.ForeignKeyConstraint(["job_id"], ["background_jobs.id"], ondelete="RESTRICT", name=op.f("fk_outbox_events_job_id_background_jobs")),
        sa.UniqueConstraint("event_key", name=op.f("uq_outbox_events_event_key")),
        sa.CheckConstraint("status IN ('pending','dispatched','failed')", name=op.f("ck_outbox_events_status_valid")),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_outbox_events_attempt_count_nonnegative")),
        sa.CheckConstraint("(dispatch_token IS NULL) = (lease_expires_at IS NULL)", name=op.f("ck_outbox_events_lease_pair")),
    )
    op.create_index("ix_outbox_events_job_id", "outbox_events", ["job_id"])
    op.create_index("ix_outbox_events_pending", "outbox_events", ["status", "next_attempt_at", "lease_expires_at"])


def downgrade():
    op.drop_index("ix_outbox_events_pending", table_name="outbox_events")
    op.drop_index("ix_outbox_events_job_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_background_jobs_status_lease", table_name="background_jobs")
    op.drop_table("background_jobs")
