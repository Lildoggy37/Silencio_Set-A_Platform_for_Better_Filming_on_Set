"""Membership-bound calendar sets and non-overlapping time intervals."""
from alembic import op
import sqlalchemy as sa

revision = "0008_availability"
down_revision = "0007_notifications"
branch_labels = None
depends_on = None

STATEMENTS = [
    "\nCREATE TABLE member_availability_sets (\n\tcrew_id UUID NOT NULL, \n\tmember_id UUID NOT NULL, \n\tmembership_joined_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlock_version INTEGER DEFAULT 1 NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_member_availability_sets PRIMARY KEY (crew_id, member_id), \n\tCONSTRAINT fk_member_availability_sets_crew_id_crew_members FOREIGN KEY(crew_id, member_id) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_member_availability_sets_version_valid CHECK (lock_version BETWEEN 1 AND 2147483647)\n)\n\n",
    "\nCREATE TABLE member_availability (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tmember_id UUID NOT NULL, \n\tstarts_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tends_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tavailability VARCHAR(20) NOT NULL, \n\tlock_version INTEGER DEFAULT 1 NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_member_availability PRIMARY KEY (id), \n\tCONSTRAINT fk_member_availability_crew_id_member_availability_sets FOREIGN KEY(crew_id, member_id) REFERENCES member_availability_sets (crew_id, member_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_member_availability_positive_interval CHECK (ends_at > starts_at), \n\tCONSTRAINT ck_member_availability_availability_valid CHECK (availability IN ('available','unavailable')), \n\tCONSTRAINT ck_member_availability_version_valid CHECK (lock_version BETWEEN 1 AND 2147483647), \n\tCONSTRAINT ex_member_availability_overlap EXCLUDE USING gist (crew_id WITH =, member_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)\n)\n\n",
    "CREATE INDEX ix_member_availability_scope_start ON member_availability (crew_id, member_id, starts_at)"
]


def upgrade():
    op.execute(sa.DDL("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement))


def downgrade():
    op.drop_table("member_availability")
    op.drop_table("member_availability_sets")
    # btree_gist may be shared by other modules; retain the extension.
