"""Published plan pointers, preflight evidence and publication journal."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='0016_plan_publish'
down_revision='0015_plan_drafts'
branch_labels=None
depends_on=None

STATEMENTS=['\nCREATE TABLE plan_preflight_checks (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tplan_version_id UUID NOT NULL, \n\tchecked_by UUID NOT NULL, \n\tmembership_joined_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tchecked_lock_version INTEGER NOT NULL, \n\texpected_current_published_version_id UUID, \n\ttoken_digest VARCHAR(64) NOT NULL, \n\tsnapshot_digest VARCHAR(64) NOT NULL, \n\tdata_snapshot JSONB NOT NULL, \n\tunknown_items JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tCONSTRAINT pk_plan_preflight_checks PRIMARY KEY (id), \n\tCONSTRAINT fk_plan_preflight_checks_crew_id_shooting_plan_versions FOREIGN KEY(crew_id, plan_version_id) REFERENCES shooting_plan_versions (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_plan_preflight_checks_crew_id_crew_members FOREIGN KEY(crew_id, checked_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_plan_check_token UNIQUE (token_digest), \n\tCONSTRAINT ck_plan_preflight_checks_expiry_valid CHECK (expires_at > created_at)\n)\n\n', '\nCREATE TABLE plan_publish_confirmations (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tplan_version_id UUID NOT NULL, \n\tconfirmed_by UUID NOT NULL, \n\tconfirmed_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tchecked_data_revision VARCHAR(64) NOT NULL, \n\tunknown_availability_snapshot JSONB NOT NULL, \n\tCONSTRAINT pk_plan_publish_confirmations PRIMARY KEY (id), \n\tCONSTRAINT fk_plan_publish_confirmations_crew_id_shooting_plan_versions FOREIGN KEY(crew_id, plan_version_id) REFERENCES shooting_plan_versions (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_plan_publish_confirmations_crew_id_crew_members FOREIGN KEY(crew_id, confirmed_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT\n)\n\n', '\nCREATE TABLE plan_publication_events (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tplan_version_id UUID NOT NULL, \n\tprevious_version_id UUID, \n\trecipient_snapshots JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tconsumed_at TIMESTAMP WITH TIME ZONE, \n\tCONSTRAINT pk_plan_publication_events PRIMARY KEY (id), \n\tCONSTRAINT fk_plan_publication_events_crew_id_shooting_plan_versions FOREIGN KEY(crew_id, plan_version_id) REFERENCES shooting_plan_versions (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_plan_publication_event UNIQUE (plan_version_id)\n)\n\n']


def upgrade():
    op.add_column('shooting_plans',sa.Column('current_published_version_id',sa.Uuid(),nullable=True))
    op.add_column('shooting_plan_versions',sa.Column('published_by',sa.Uuid(),nullable=True))
    op.add_column('shooting_plan_versions',sa.Column('published_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('shooting_plan_versions',sa.Column('publication_snapshot',postgresql.JSONB(),nullable=True))
    op.add_column('shooting_plan_versions',sa.Column('review_reasons',postgresql.JSONB(),server_default=sa.text("'[]'::jsonb"),nullable=False))
    op.drop_constraint(op.f('ck_shooting_plan_versions_draft_only'),'shooting_plan_versions',type_='check')
    op.create_check_constraint('status_valid','shooting_plan_versions',"status IN ('draft','published')")
    op.create_check_constraint('publication_valid','shooting_plan_versions',"(status = 'draft' AND published_by IS NULL AND published_at IS NULL AND publication_snapshot IS NULL) OR (status = 'published' AND published_by IS NOT NULL AND published_at IS NOT NULL AND publication_snapshot IS NOT NULL)")
    op.create_unique_constraint('uq_plan_version_parent','shooting_plan_versions',['crew_id','plan_id','id'])
    op.create_foreign_key('fk_plan_publisher_member','shooting_plan_versions','crew_members',['crew_id','published_by'],['crew_id','id'],ondelete='RESTRICT')
    op.create_foreign_key('fk_plan_current_publication','shooting_plans','shooting_plan_versions',['crew_id','id','current_published_version_id'],['crew_id','plan_id','id'],ondelete='RESTRICT')
    for sql in STATEMENTS: op.execute(sa.DDL(sql))


def downgrade():
    # Published data must not be silently discarded to fit the old draft-only schema.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM shooting_plan_versions WHERE status = 'published') THEN RAISE EXCEPTION 'Resolve published plans before downgrading'; END IF; END $$")
    op.drop_table('plan_publication_events')
    op.drop_table('plan_publish_confirmations')
    op.drop_table('plan_preflight_checks')
    op.drop_constraint('fk_plan_current_publication','shooting_plans',type_='foreignkey')
    op.drop_constraint('fk_plan_publisher_member','shooting_plan_versions',type_='foreignkey')
    op.drop_constraint('uq_plan_version_parent','shooting_plan_versions',type_='unique')
    op.drop_constraint(op.f('ck_shooting_plan_versions_publication_valid'),'shooting_plan_versions',type_='check')
    op.drop_constraint(op.f('ck_shooting_plan_versions_status_valid'),'shooting_plan_versions',type_='check')
    op.create_check_constraint('draft_only','shooting_plan_versions',"status = 'draft'")
    for column in ('review_reasons','publication_snapshot','published_at','published_by'): op.drop_column('shooting_plan_versions',column)
    op.drop_column('shooting_plans','current_published_version_id')
