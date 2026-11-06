"""Persist private task proposals and their confirmation results."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0026_task_proposals'
down_revision = '0025_workflow_policies'
branch_labels = depends_on = None


def upgrade():
    op.create_table('diane_task_proposals',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('crew_id', sa.Uuid(), sa.ForeignKey('crews.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('member_id', sa.Uuid(), sa.ForeignKey('crew_members.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('membership_joined_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(20), nullable=False), sa.Column('lock_version', sa.Integer(), nullable=False),
        sa.Column('tasks', postgresql.JSONB(), nullable=False), sa.Column('member_snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('generation', postgresql.JSONB()), sa.Column('created_task_ids', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False), sa.Column('decided_at', sa.DateTime(timezone=True)),
        sa.Column('decision_version', sa.Integer()),
        sa.CheckConstraint("status IN ('pending','completed','rejected')", name=op.f('ck_diane_task_proposals_status_valid')),
        sa.CheckConstraint('lock_version >= 1', name=op.f('ck_diane_task_proposals_version_positive')),
        sa.CheckConstraint("jsonb_typeof(tasks) = 'array' AND jsonb_array_length(tasks) BETWEEN 1 AND 5", name=op.f('ck_diane_task_proposals_task_count')),
        sa.CheckConstraint("(status = 'pending' AND decided_at IS NULL AND decision_version IS NULL) OR (status <> 'pending' AND decided_at IS NOT NULL AND decision_version IS NOT NULL)", name=op.f('ck_diane_task_proposals_decision_pair')))
    op.create_index('ix_diane_task_proposals_crew_id', 'diane_task_proposals', ['crew_id'])
    op.create_index('ix_diane_task_proposals_user_id', 'diane_task_proposals', ['user_id'])


def downgrade():
    op.drop_table('diane_task_proposals')
