"""Versioned scene characters and resource needs."""
from alembic import op
import sqlalchemy as sa

revision = '0014_scene_needs'
down_revision = '0013_creative'
branch_labels = None
depends_on = None

STATEMENTS = ['\nCREATE TABLE scene_characters (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tscene_revision_id UUID NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tCONSTRAINT pk_scene_characters PRIMARY KEY (id), \n\tCONSTRAINT fk_scene_characters_crew_id_scene_revisions FOREIGN KEY(crew_id, scene_revision_id) REFERENCES scene_revisions (crew_id, id) ON DELETE CASCADE, \n\tCONSTRAINT uq_scene_character_name UNIQUE (scene_revision_id, name), \n\tCONSTRAINT ck_scene_characters_name_valid CHECK (char_length(name) BETWEEN 1 AND 100)\n)\n\n', "\nCREATE TABLE scene_resource_needs (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tscene_revision_id UUID NOT NULL, \n\tresource_type VARCHAR(20) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tquantity INTEGER NOT NULL, \n\tunit VARCHAR(10) NOT NULL, \n\tresource_id UUID, \n\tCONSTRAINT pk_scene_resource_needs PRIMARY KEY (id), \n\tCONSTRAINT fk_scene_resource_needs_crew_id_scene_revisions FOREIGN KEY(crew_id, scene_revision_id) REFERENCES scene_revisions (crew_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_scene_resource_needs_crew_id_resources FOREIGN KEY(crew_id, resource_id) REFERENCES resources (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_scene_need_name UNIQUE (scene_revision_id, resource_type, name), \n\tCONSTRAINT uq_scene_need_resource UNIQUE (scene_revision_id, resource_id), \n\tCONSTRAINT ck_scene_resource_needs_type_valid CHECK (resource_type IN ('equipment','prop','location','consumable')), \n\tCONSTRAINT ck_scene_resource_needs_quantity_valid CHECK (quantity BETWEEN 1 AND 1000000), \n\tCONSTRAINT ck_scene_resource_needs_text_valid CHECK (char_length(name) BETWEEN 1 AND 100 AND char_length(unit) BETWEEN 1 AND 10)\n)\n\n"]


def upgrade():
    for sql in STATEMENTS:
        op.execute(sa.DDL(sql))


def downgrade():
    op.drop_table('scene_resource_needs')
    op.drop_table('scene_characters')
