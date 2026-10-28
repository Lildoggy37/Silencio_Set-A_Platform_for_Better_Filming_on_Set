"""Private notification delivery and membership-bound read receipts."""
from alembic import op
import sqlalchemy as sa

revision = "0007_notifications"
down_revision = "0006_tasks"
branch_labels = None
depends_on = None

STATEMENTS = [
    "\nCREATE TABLE notifications (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tsource_event_id UUID NOT NULL, \n\tevent_key VARCHAR(100) NOT NULL, \n\tevent_type VARCHAR(50) NOT NULL, \n\ttitle VARCHAR(100) NOT NULL, \n\tsummary VARCHAR(2000) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_notifications PRIMARY KEY (id), \n\tCONSTRAINT uq_notifications_crew_id_id UNIQUE (crew_id, id), \n\tCONSTRAINT uq_notifications_crew_event UNIQUE (crew_id, event_key), \n\tCONSTRAINT uq_notifications_source_event UNIQUE (source_event_id), \n\tCONSTRAINT fk_notifications_crew_id_task_change_events FOREIGN KEY(crew_id, source_event_id) REFERENCES task_change_events (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_notifications_title_valid CHECK (char_length(title) BETWEEN 1 AND 100), \n\tCONSTRAINT ck_notifications_summary_valid CHECK (char_length(summary) BETWEEN 1 AND 2000)\n)\n\n",
    "CREATE INDEX ix_notifications_crew_created ON notifications (crew_id, created_at, id)",
    "\nCREATE TABLE notification_recipients (\n\tcrew_id UUID NOT NULL, \n\tnotification_id UUID NOT NULL, \n\tmember_id UUID NOT NULL, \n\tmembership_joined_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trequires_confirmation BOOLEAN DEFAULT 'false' NOT NULL, \n\tread_at TIMESTAMP WITH TIME ZONE, \n\tconfirmed_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_notification_recipients PRIMARY KEY (crew_id, notification_id, member_id), \n\tCONSTRAINT fk_notification_recipients_crew_id_notifications FOREIGN KEY(crew_id, notification_id) REFERENCES notifications (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_notification_recipients_crew_id_crew_members FOREIGN KEY(crew_id, member_id) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_notification_recipients_confirmation_pair CHECK (confirmed_at IS NULL OR (requires_confirmation AND read_at IS NOT NULL AND confirmed_at >= read_at))\n)\n\n",
    "CREATE INDEX ix_notification_recipients_member_read ON notification_recipients (crew_id, member_id, read_at, notification_id)"
]


def upgrade():
    op.create_unique_constraint("uq_task_events_crew_id_id", "task_change_events", ["crew_id", "id"])
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement))


def downgrade():
    op.drop_table("notification_recipients")
    op.drop_table("notifications")
    op.drop_constraint("uq_task_events_crew_id_id", "task_change_events", type_="unique")
