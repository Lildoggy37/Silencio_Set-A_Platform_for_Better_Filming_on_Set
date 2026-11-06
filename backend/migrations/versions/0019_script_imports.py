"""Private original script files and same-script version provenance."""
from alembic import op
import sqlalchemy as sa

revision = '0019_script_imports'
down_revision = '0018_publication_notices'
branch_labels = None
depends_on = None

CREATE_FILE_TABLE = "\nCREATE TABLE script_source_files (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tscript_id UUID NOT NULL, \n\tcreated_by UUID NOT NULL, \n\toriginal_name VARCHAR(200) NOT NULL, \n\tfile_format VARCHAR(10) NOT NULL, \n\tbyte_size INTEGER NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tstorage_key VARCHAR(100) NOT NULL, \n\tpage_count INTEGER, \n\textraction_notes JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_script_source_files PRIMARY KEY (id), \n\tCONSTRAINT uq_script_file_scope UNIQUE (crew_id, script_id, id), \n\tCONSTRAINT fk_script_source_files_crew_id_scripts FOREIGN KEY(crew_id, script_id) REFERENCES scripts (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_script_source_files_crew_id_crew_members FOREIGN KEY(crew_id, created_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_script_source_files_file_valid CHECK (file_format IN ('docx','pdf','txt') AND byte_size BETWEEN 1 AND 20971520), \n\tCONSTRAINT uq_script_source_files_storage_key UNIQUE (storage_key)\n)\n\n"


def upgrade():
    op.execute(sa.DDL(CREATE_FILE_TABLE))
    op.add_column('script_versions', sa.Column('source_file_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_script_version_source_file', 'script_versions', 'script_source_files',
        ['crew_id', 'script_id', 'source_file_id'], ['crew_id', 'script_id', 'id'], ondelete='RESTRICT')


def downgrade():
    op.drop_constraint('fk_script_version_source_file', 'script_versions', type_='foreignkey')
    op.drop_column('script_versions', 'source_file_id')
    op.drop_table('script_source_files')
