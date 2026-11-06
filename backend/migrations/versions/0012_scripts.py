"""Backend business schema snapshot."""
from alembic import op
import sqlalchemy as sa

revision = '0012_scripts'
down_revision = '0011_stock'
branch_labels = None
depends_on = None

STATEMENTS = [
    "\nCREATE TABLE scripts (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\ttitle VARCHAR(100) NOT NULL, \n\tcurrent_version_id UUID, \n\tcreated_by UUID NOT NULL, \n\tlock_version INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_scripts PRIMARY KEY (id), \n\tCONSTRAINT uq_scripts_crew_id UNIQUE (crew_id, id), \n\tCONSTRAINT fk_scripts_crew_id_crew_members FOREIGN KEY(crew_id, created_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_scripts_version_valid CHECK (lock_version BETWEEN 1 AND 2147483647), \n\tCONSTRAINT fk_scripts_crew_id_crews FOREIGN KEY(crew_id) REFERENCES crews (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE script_versions (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tscript_id UUID NOT NULL, \n\tversion_no INTEGER NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tconfirmed_by UUID, \n\tconfirmed_at TIMESTAMP WITH TIME ZONE, \n\tcreated_by UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tlock_version INTEGER NOT NULL, \n\tCONSTRAINT pk_script_versions PRIMARY KEY (id), \n\tCONSTRAINT uq_script_versions_scope UNIQUE (crew_id, script_id, id), \n\tCONSTRAINT uq_script_versions_crew_id UNIQUE (crew_id, id), \n\tCONSTRAINT uq_script_versions_number UNIQUE (script_id, version_no), \n\tCONSTRAINT fk_script_versions_crew_id_scripts FOREIGN KEY(crew_id, script_id) REFERENCES scripts (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_script_versions_created_by FOREIGN KEY(crew_id, created_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_script_versions_confirmed_by FOREIGN KEY(crew_id, confirmed_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_script_versions_status_valid CHECK (status IN ('pending_check','confirmed')), \n\tCONSTRAINT ck_script_versions_confirmation_pair CHECK ((status = 'confirmed' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL) OR (status = 'pending_check' AND confirmed_by IS NULL AND confirmed_at IS NULL)), \n\tCONSTRAINT ck_script_versions_version_valid CHECK (version_no > 0 AND lock_version BETWEEN 1 AND 2147483647)\n)\n\n",
    "\nCREATE TABLE script_paragraphs (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tscript_version_id UUID NOT NULL, \n\tparagraph_no INTEGER NOT NULL, \n\ttext TEXT NOT NULL, \n\tsource_page INTEGER, \n\tCONSTRAINT pk_script_paragraphs PRIMARY KEY (id), \n\tCONSTRAINT fk_script_paragraphs_crew_id_script_versions FOREIGN KEY(crew_id, script_version_id) REFERENCES script_versions (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_script_paragraph_number UNIQUE (script_version_id, paragraph_no), \n\tCONSTRAINT ck_script_paragraphs_paragraph_valid CHECK (paragraph_no > 0 AND char_length(text) > 0)\n)\n\n",
    "\nCREATE TABLE script_action_receipts (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tmembership_joined_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tscript_id UUID NOT NULL, \n\tkey_digest VARCHAR(64) NOT NULL, \n\trequest_digest VARCHAR(64) NOT NULL, \n\tresponse_snapshot JSONB NOT NULL, \n\tCONSTRAINT pk_script_action_receipts PRIMARY KEY (id), \n\tCONSTRAINT uq_script_receipts_key UNIQUE (crew_id, user_id, key_digest), \n\tCONSTRAINT fk_script_action_receipts_crew_id_scripts FOREIGN KEY(crew_id, script_id) REFERENCES scripts (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_script_action_receipts_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n",
    "ALTER TABLE scripts ADD CONSTRAINT fk_script_current_version FOREIGN KEY(crew_id, id, current_version_id) REFERENCES script_versions (crew_id, script_id, id) ON DELETE RESTRICT"
]

def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement))

def downgrade():
    op.drop_constraint("fk_script_current_version", "scripts", type_="foreignkey")
    op.drop_table("script_action_receipts")
    op.drop_table("script_paragraphs")
    op.drop_table("script_versions")
    op.drop_table("scripts")
