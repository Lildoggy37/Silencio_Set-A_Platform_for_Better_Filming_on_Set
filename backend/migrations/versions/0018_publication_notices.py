"""Extend private notifications to formal plans and call sheet lifecycle events."""
from alembic import op
import sqlalchemy as sa

revision = '0018_publication_notices'
down_revision = '0017_call_sheets'
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint('uq_plan_publication_event_scope', 'plan_publication_events', ['crew_id', 'id'])
    op.create_unique_constraint('uq_call_sheet_event_scope', 'call_sheet_events', ['crew_id', 'id'])
    op.alter_column('notifications', 'source_event_id', existing_type=sa.Uuid(), nullable=True)
    op.add_column('notifications', sa.Column('plan_event_id', sa.Uuid(), nullable=True))
    op.add_column('notifications', sa.Column('call_sheet_event_id', sa.Uuid(), nullable=True))
    for field, target, suffix in [('plan_event_id', 'plan_publication_events', 'plan'), ('call_sheet_event_id', 'call_sheet_events', 'call_sheet')]:
        op.create_unique_constraint(f'uq_notifications_{suffix}_event', 'notifications', [field])
        op.create_foreign_key(f'fk_notifications_{suffix}_event', 'notifications', target, ['crew_id', field], ['crew_id', 'id'], ondelete='RESTRICT')
    op.create_check_constraint('one_source', 'notifications', 'num_nonnulls(source_event_id, plan_event_id, call_sheet_event_id) = 1')
    op.create_index('ix_plan_events_pending', 'plan_publication_events', ['created_at', 'id'], postgresql_where=sa.text('consumed_at IS NULL'))
    op.create_index('ix_call_sheet_events_pending', 'call_sheet_events', ['created_at', 'id'], postgresql_where=sa.text('consumed_at IS NULL'))
    op.create_index('ix_task_events_pending', 'task_change_events', ['created_at', 'id'], postgresql_where=sa.text("dispatch_status = 'pending'"))


def downgrade():
    # Refuse before dropping publication columns if publication notices exist.
    op.alter_column('notifications', 'source_event_id', existing_type=sa.Uuid(), nullable=False)
    op.drop_index('ix_task_events_pending', table_name='task_change_events')
    op.drop_index('ix_call_sheet_events_pending', table_name='call_sheet_events')
    op.drop_index('ix_plan_events_pending', table_name='plan_publication_events')
    op.drop_constraint(op.f('ck_notifications_one_source'), 'notifications', type_='check')
    for field, suffix in [('call_sheet_event_id', 'call_sheet'), ('plan_event_id', 'plan')]:
        op.drop_constraint(f'fk_notifications_{suffix}_event', 'notifications', type_='foreignkey')
        op.drop_constraint(f'uq_notifications_{suffix}_event', 'notifications', type_='unique')
        op.drop_column('notifications', field)
    op.drop_constraint('uq_call_sheet_event_scope', 'call_sheet_events', type_='unique')
    op.drop_constraint('uq_plan_publication_event_scope', 'plan_publication_events', type_='unique')
