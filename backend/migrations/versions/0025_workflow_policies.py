"""Support optional task acceptance and resource reservation policies."""
from alembic import op
import sqlalchemy as sa

revision = "0025_workflow_policies"
down_revision = "0024_impact_history"
branch_labels = None
depends_on = None

REVIEW_PAIR = "(review_status IN ('pending','completed') AND reviewed_by IS NULL AND reviewed_at IS NULL) OR (review_status NOT IN ('pending','completed') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND reviewed_at >= submitted_at)"
BOOKING_REVIEW = "(status NOT IN ('confirmed','rejected') OR reviewed_by IS NOT NULL OR (status = 'confirmed' AND confirmation_mode = 'automatic')) AND (confirmation_mode <> 'automatic' OR reviewed_by IS NULL)"


def upgrade():
    # Existing objects retain their review requirements.
    op.add_column("tasks", sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("resources", sa.Column("approval_policy", sa.String(20), nullable=False, server_default="manual"))
    op.create_check_constraint(op.f("ck_resources_approval_policy_valid"), "resources", "approval_policy IN ('manual','automatic')")
    op.add_column("resource_reservations", sa.Column("confirmation_mode", sa.String(20), nullable=False, server_default="manual"))
    op.create_check_constraint(op.f("ck_resource_reservations_confirmation_mode_valid"), "resource_reservations", "confirmation_mode IN ('manual','automatic')")
    op.drop_constraint(op.f("ck_resource_reservations_reviewed_state"), "resource_reservations", type_="check")
    op.create_check_constraint(op.f("ck_resource_reservations_reviewed_state"), "resource_reservations", BOOKING_REVIEW)
    op.drop_constraint(op.f("ck_task_submissions_review_status_valid"), "task_submissions", type_="check")
    op.drop_constraint(op.f("ck_task_submissions_review_pair"), "task_submissions", type_="check")
    op.create_check_constraint(op.f("ck_task_submissions_review_status_valid"), "task_submissions", "review_status IN ('pending','accepted','rejected','cancelled','completed')")
    op.create_check_constraint(op.f("ck_task_submissions_review_pair"), "task_submissions", REVIEW_PAIR)


def downgrade():
    # The former constraints reject new workflow records rather than rewriting
    # completion or confirmation history. PostgreSQL rolls back on failure.
    op.drop_constraint(op.f("ck_task_submissions_review_pair"), "task_submissions", type_="check")
    op.drop_constraint(op.f("ck_task_submissions_review_status_valid"), "task_submissions", type_="check")
    op.create_check_constraint(op.f("ck_task_submissions_review_status_valid"), "task_submissions", "review_status IN ('pending','accepted','rejected','cancelled')")
    op.create_check_constraint(op.f("ck_task_submissions_review_pair"), "task_submissions", "(review_status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL) OR (review_status <> 'pending' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND reviewed_at >= submitted_at)")
    op.drop_constraint(op.f("ck_resource_reservations_reviewed_state"), "resource_reservations", type_="check")
    op.create_check_constraint(op.f("ck_resource_reservations_reviewed_state"), "resource_reservations", "status NOT IN ('confirmed','rejected') OR reviewed_by IS NOT NULL")
    op.drop_constraint(op.f("ck_resource_reservations_confirmation_mode_valid"), "resource_reservations", type_="check")
    op.drop_column("resource_reservations", "confirmation_mode")
    op.drop_constraint(op.f("ck_resources_approval_policy_valid"), "resources", type_="check")
    op.drop_column("resources", "approval_policy")
    op.drop_column("tasks", "requires_review")
