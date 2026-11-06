"""Durable Agent queue and observable progress on conversation turns."""
from alembic import op
import sqlalchemy as sa

revision = '0028_diane_background'
down_revision = '0027_diane_memory'
branch_labels = depends_on = None


def upgrade():
    table = 'diane_conversation_turns'
    op.drop_constraint(op.f('ck_diane_conversation_turns_status_valid'), table, type_='check')
    op.create_check_constraint(op.f('ck_diane_conversation_turns_status_valid'), table,
        "status IN ('queued','running','completed','failed','interrupted')")
    op.add_column(table, sa.Column('phase', sa.String(30), nullable=False, server_default='model'))
    op.add_column(table, sa.Column('progress_version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column(table, sa.Column('request_id', sa.String(32)))
    op.add_column(table, sa.Column('dispatch_after', sa.DateTime(timezone=True)))
    op.add_column(table, sa.Column('dispatched_at', sa.DateTime(timezone=True)))
    op.add_column(table, sa.Column('execution_token', sa.Uuid()))
    op.execute("UPDATE diane_conversation_turns SET phase=status WHERE status <> 'running'")
    op.create_check_constraint(op.f('ck_diane_conversation_turns_progress_version_positive'), table, 'progress_version >= 1')
    op.create_index('ix_diane_conversation_turns_dispatch_after', table, ['dispatch_after'])
    op.alter_column(table, 'phase', server_default=None)
    op.alter_column(table, 'progress_version', server_default=None)


def downgrade():
    table = 'diane_conversation_turns'
    op.execute("UPDATE diane_conversation_turns SET status='interrupted' WHERE status='queued'")
    op.drop_constraint(op.f('ck_diane_conversation_turns_status_valid'), table, type_='check')
    op.create_check_constraint(op.f('ck_diane_conversation_turns_status_valid'), table,
        "status IN ('running','completed','failed','interrupted')")
    op.drop_index('ix_diane_conversation_turns_dispatch_after', table)
    op.drop_constraint(op.f('ck_diane_conversation_turns_progress_version_positive'), table, type_='check')
    for name in ['execution_token','dispatched_at','dispatch_after','request_id','progress_version','phase']:
        op.drop_column(table, name)
