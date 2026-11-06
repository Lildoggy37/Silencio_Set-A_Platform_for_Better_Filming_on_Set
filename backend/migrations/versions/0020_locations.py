"""Location scouting details and private normalized photo metadata."""
from alembic import op
import sqlalchemy as sa

revision = '0020_locations'
down_revision = '0019_script_imports'
branch_labels = None
depends_on = None

STATEMENTS = ['\nCREATE TABLE location_details (\n\tlocation_resource_id UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\taddress VARCHAR(300), \n\taccess_instructions VARCHAR(1000), \n\tshooting_constraints VARCHAR(2000), \n\tCONSTRAINT pk_location_details PRIMARY KEY (location_resource_id), \n\tCONSTRAINT fk_location_details_crew_id_resources FOREIGN KEY(crew_id, location_resource_id) REFERENCES resources (crew_id, id) ON DELETE RESTRICT\n)\n\n', "\nCREATE TABLE location_photos (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tlocation_resource_id UUID NOT NULL, \n\tcreated_by UUID NOT NULL, \n\tdescription VARCHAR(500), \n\tfile_format VARCHAR(10) NOT NULL, \n\tbyte_size INTEGER NOT NULL, \n\twidth_px INTEGER NOT NULL, \n\theight_px INTEGER NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tstorage_key VARCHAR(100) NOT NULL, \n\tpreview_storage_key VARCHAR(100) NOT NULL, \n\tpreview_byte_size INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tremoved_at TIMESTAMP WITH TIME ZONE, \n\tremoved_by UUID, \n\tCONSTRAINT pk_location_photos PRIMARY KEY (id), \n\tCONSTRAINT fk_location_photos_crew_id_resources FOREIGN KEY(crew_id, location_resource_id) REFERENCES resources (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_location_photos_created_by FOREIGN KEY(crew_id, created_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_location_photos_removed_by FOREIGN KEY(crew_id, removed_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_location_photos_file_valid CHECK (file_format IN ('jpeg','png','webp') AND byte_size BETWEEN 1 AND 10485760), \n\tCONSTRAINT ck_location_photos_pixels_valid CHECK (width_px > 0 AND height_px > 0 AND width_px::bigint * height_px <= 25000000), \n\tCONSTRAINT ck_location_photos_preview_size_valid CHECK (preview_byte_size BETWEEN 1 AND 10485760), \n\tCONSTRAINT ck_location_photos_removal_pair CHECK ((removed_at IS NULL) = (removed_by IS NULL)), \n\tCONSTRAINT uq_location_photos_storage_key UNIQUE (storage_key), \n\tCONSTRAINT uq_location_photos_preview_storage_key UNIQUE (preview_storage_key)\n)\n\n", 'CREATE INDEX ix_location_photos_active ON location_photos (crew_id, location_resource_id, created_at, id) WHERE removed_at IS NULL']


def upgrade():
    for statement in STATEMENTS: op.execute(sa.DDL(statement))


def downgrade():
    op.drop_table('location_photos')
    op.drop_table('location_details')
