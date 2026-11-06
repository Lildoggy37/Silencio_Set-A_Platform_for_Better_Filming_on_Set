"""Physical checkouts and partial returns."""
from alembic import op
import sqlalchemy as sa

revision = "0010_custody"
down_revision = "0009_resources"
branch_labels = None
depends_on = None

STATEMENTS = [
    "\nCREATE TABLE resource_checkouts (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tresource_id UUID NOT NULL, \n\treservation_id UUID NOT NULL, \n\tborrower_member_id UUID NOT NULL, \n\tborrower_membership_joined_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tquantity INTEGER NOT NULL, \n\treturned_quantity INTEGER DEFAULT 0 NOT NULL, \n\tchecked_out_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpected_return_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\thandled_by UUID NOT NULL, \n\tlock_version INTEGER DEFAULT 1 NOT NULL, \n\tCONSTRAINT pk_resource_checkouts PRIMARY KEY (id), \n\tCONSTRAINT uq_checkouts_crew_id UNIQUE (crew_id, id), \n\tCONSTRAINT fk_resource_checkouts_crew_id_resources FOREIGN KEY(crew_id, resource_id) REFERENCES resources (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_resource_checkouts_crew_id_resource_reservations FOREIGN KEY(crew_id, reservation_id) REFERENCES resource_reservations (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_checkouts_borrower_member_id FOREIGN KEY(crew_id, borrower_member_id) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_checkouts_handled_by FOREIGN KEY(crew_id, handled_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_resource_checkouts_quantity_valid CHECK (quantity BETWEEN 1 AND 1000000 AND returned_quantity BETWEEN 0 AND quantity), \n\tCONSTRAINT ck_resource_checkouts_return_time_valid CHECK (expected_return_at >= checked_out_at), \n\tCONSTRAINT ck_resource_checkouts_version_valid CHECK (lock_version BETWEEN 1 AND 2147483647)\n)\n\n",
    "CREATE INDEX ix_checkouts_reservation ON resource_checkouts (crew_id, reservation_id)",
    "CREATE INDEX ix_checkouts_resource ON resource_checkouts (crew_id, resource_id)",
    "\nCREATE TABLE resource_returns (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tcheckout_id UUID NOT NULL, \n\tquantity INTEGER NOT NULL, \n\treturned_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\thandled_by UUID NOT NULL, \n\tnote VARCHAR(500), \n\tCONSTRAINT pk_resource_returns PRIMARY KEY (id), \n\tCONSTRAINT fk_resource_returns_crew_id_resource_checkouts FOREIGN KEY(crew_id, checkout_id) REFERENCES resource_checkouts (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_resource_returns_crew_id_crew_members FOREIGN KEY(crew_id, handled_by) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_resource_returns_quantity_valid CHECK (quantity BETWEEN 1 AND 1000000), \n\tCONSTRAINT ck_resource_returns_note_valid CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 500)\n)\n\n",
    "CREATE INDEX ix_returns_checkout ON resource_returns (crew_id, checkout_id)"
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement))


def downgrade():
    op.drop_table("resource_returns")
    op.drop_table("resource_checkouts")
