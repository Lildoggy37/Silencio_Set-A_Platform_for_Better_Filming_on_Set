"""Backend business schema snapshot."""
from alembic import op
import sqlalchemy as sa

revision = '0011_stock'
down_revision = '0010_custody'
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE resources ADD COLUMN stock_quantity INTEGER",
    "ALTER TABLE resources ALTER COLUMN total_quantity DROP NOT NULL",
    "ALTER TABLE resources DROP CONSTRAINT ck_resources_type_valid",
    "ALTER TABLE resources DROP CONSTRAINT ck_resources_quantity_valid",
    "ALTER TABLE resources ADD CONSTRAINT ck_resources_quantity_valid CHECK ((resource_type = 'consumable' AND total_quantity IS NULL AND stock_quantity BETWEEN 0 AND 2147483647 AND stock_quantity IS NOT NULL) OR (resource_type <> 'consumable' AND total_quantity BETWEEN 1 AND 1000000 AND total_quantity IS NOT NULL AND stock_quantity IS NULL))",
    "ALTER TABLE resources ADD CONSTRAINT ck_resources_type_valid CHECK (resource_type IN ('equipment','prop','location','consumable'))",
    "\nCREATE TABLE consumable_movements (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tresource_id UUID NOT NULL, \n\tquantity_delta INTEGER NOT NULL, \n\tbalance_after INTEGER NOT NULL, \n\tmovement_type VARCHAR(20) NOT NULL, \n\treason VARCHAR(500) NOT NULL, \n\toperated_by UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_consumable_movements PRIMARY KEY (id), \n\tCONSTRAINT fk_consumable_movements_crew_id_resources FOREIGN KEY(crew_id, resource_id) REFERENCES resources (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_consumable_movements_crew_id_crew_members FOREIGN KEY(crew_id, operated_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_consumable_movements_quantity_valid CHECK (quantity_delta <> 0 AND balance_after BETWEEN 0 AND 2147483647), \n\tCONSTRAINT ck_consumable_movements_type_direction_valid CHECK ((movement_type IN ('initial','restock','return') AND quantity_delta > 0) OR (movement_type = 'issue' AND quantity_delta < 0) OR movement_type = 'adjustment')\n)\n\n",
    "CREATE INDEX ix_consumable_movements_resource ON consumable_movements (crew_id, resource_id, created_at, id)"
]

def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement))

def downgrade():
    op.drop_table("consumable_movements")
    op.execute(sa.DDL("ALTER TABLE resources DROP CONSTRAINT ck_resources_quantity_valid"))
    op.execute(sa.DDL("ALTER TABLE resources DROP CONSTRAINT ck_resources_type_valid"))
    op.execute(sa.DDL("ALTER TABLE resources ADD CONSTRAINT ck_resources_type_valid CHECK (resource_type IN ('equipment','prop','location'))"))
    op.execute(sa.DDL("ALTER TABLE resources ADD CONSTRAINT ck_resources_quantity_valid CHECK (total_quantity BETWEEN 1 AND 1000000)"))
    op.alter_column("resources", "total_quantity", nullable=False)
    op.drop_column("resources", "stock_quantity")
