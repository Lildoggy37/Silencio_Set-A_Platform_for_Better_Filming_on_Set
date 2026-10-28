"""Crew invitations; seven-day credentials are never stored as plaintext."""
from alembic import op
import sqlalchemy as sa

revision = "0005_invitations"
down_revision = "0004_crews"
branch_labels = None
depends_on = None

STATEMENTS = [
    "\nCREATE TABLE crew_invitations (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\temail_normalized VARCHAR(254) NOT NULL, \n\ttoken_digest TEXT NOT NULL, \n\tinvited_by_member_id UUID NOT NULL, \n\tstatus VARCHAR(20) DEFAULT 'pending' NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\taccepted_by_user_id UUID, \n\taccepted_at TIMESTAMP WITH TIME ZONE, \n\tlock_version INTEGER DEFAULT 1 NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_crew_invitations PRIMARY KEY (id), \n\tCONSTRAINT fk_crew_invitations_crew_id_crew_members FOREIGN KEY(crew_id, invited_by_member_id) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_crew_invitations_status_valid CHECK (status IN ('pending','accepted','revoked','expired')), \n\tCONSTRAINT ck_crew_invitations_lock_version_positive CHECK (lock_version >= 1), \n\tCONSTRAINT ck_crew_invitations_email_valid CHECK (char_length(email_normalized) BETWEEN 1 AND 254), \n\tCONSTRAINT ck_crew_invitations_token_digest_valid CHECK (token_digest ~ '^[a-f0-9]{64}$'), \n\tCONSTRAINT ck_crew_invitations_expiry_after_creation CHECK (expires_at > created_at), \n\tCONSTRAINT ck_crew_invitations_acceptance_pair CHECK ((status = 'accepted' AND accepted_by_user_id IS NOT NULL AND accepted_at IS NOT NULL) OR (status <> 'accepted' AND accepted_by_user_id IS NULL AND accepted_at IS NULL)), \n\tCONSTRAINT fk_crew_invitations_crew_id_crews FOREIGN KEY(crew_id) REFERENCES crews (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_crew_invitations_token_digest UNIQUE (token_digest), \n\tCONSTRAINT fk_crew_invitations_accepted_by_user_id_users FOREIGN KEY(accepted_by_user_id) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE UNIQUE INDEX uq_crew_invitations_pending_email ON crew_invitations (crew_id, email_normalized) WHERE status = 'pending'"
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement))


def downgrade():
    op.drop_table("crew_invitations")
