"""Preserve withdrawn plans and emit a separate cancellation event."""
from alembic import op
import sqlalchemy as sa

revision = '0022_plan_withdrawal'
down_revision = '0021_casting'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('shooting_plan_versions', sa.Column('withdrawn_at', sa.DateTime(timezone=True)))
    op.add_column('shooting_plan_versions', sa.Column('withdrawn_by', sa.Uuid()))
    op.add_column('shooting_plan_versions', sa.Column('withdrawal_reason', sa.String(500)))
    op.create_foreign_key('fk_plan_withdrawer_member', 'shooting_plan_versions', 'crew_members', ['crew_id', 'withdrawn_by'], ['crew_id', 'id'], ondelete='RESTRICT')
    op.create_check_constraint(op.f('ck_shooting_plan_versions_withdrawal_valid'), 'shooting_plan_versions',
        "(withdrawn_at IS NULL AND withdrawn_by IS NULL AND withdrawal_reason IS NULL) OR (status = 'published' AND withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL AND withdrawal_reason IS NOT NULL AND char_length(withdrawal_reason) BETWEEN 1 AND 500)")
    op.add_column('plan_publication_events', sa.Column('kind', sa.String(20), server_default='published', nullable=False))
    op.drop_constraint('uq_plan_publication_event', 'plan_publication_events', type_='unique')
    op.create_unique_constraint('uq_plan_event_kind', 'plan_publication_events', ['plan_version_id', 'kind'])
    op.create_check_constraint(op.f('ck_plan_publication_events_kind_valid'), 'plan_publication_events', "kind IN ('published','withdrawn')")


def downgrade():
    # A version with both events cannot be downgraded without losing history.
    op.drop_constraint(op.f('ck_plan_publication_events_kind_valid'), 'plan_publication_events', type_='check')
    op.drop_constraint('uq_plan_event_kind', 'plan_publication_events', type_='unique')
    op.create_unique_constraint('uq_plan_publication_event', 'plan_publication_events', ['plan_version_id'])
    op.drop_column('plan_publication_events', 'kind')
    op.drop_constraint(op.f('ck_shooting_plan_versions_withdrawal_valid'), 'shooting_plan_versions', type_='check')
    op.drop_constraint('fk_plan_withdrawer_member', 'shooting_plan_versions', type_='foreignkey')
    for name in ('withdrawal_reason', 'withdrawn_by', 'withdrawn_at'):
        op.drop_column('shooting_plan_versions', name)
