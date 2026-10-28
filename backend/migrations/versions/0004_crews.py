"""Frozen PostgreSQL DDL for crew organization and its fixed catalog.

Generated from the reviewed model for this revision; no live metadata imports.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_crews"
down_revision = "0003_mail_jobs"
branch_labels = None
depends_on = None

STATEMENTS = [
    "\nCREATE TABLE permissions (\n\tcode VARCHAR(100) NOT NULL, \n\tdescription VARCHAR(500) NOT NULL, \n\tCONSTRAINT pk_permissions PRIMARY KEY (code)\n)\n\n",
    "\nCREATE TABLE roles (\n\tcode VARCHAR(32) NOT NULL, \n\tname VARCHAR(30) NOT NULL, \n\tCONSTRAINT pk_roles PRIMARY KEY (code)\n)\n\n",
    "\nCREATE TABLE crews (\n\tid UUID NOT NULL, \n\tname VARCHAR(50) NOT NULL, \n\tproject_name VARCHAR(100) NOT NULL, \n\tdescription VARCHAR(1000), \n\tcreated_by UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_crews PRIMARY KEY (id), \n\tCONSTRAINT ck_crews_name_valid CHECK (char_length(name) BETWEEN 1 AND 50 AND name = btrim(name) AND name !~ '^[[:space:]]*$' AND name !~ '[[:cntrl:]]'), \n\tCONSTRAINT ck_crews_project_name_valid CHECK (char_length(project_name) BETWEEN 1 AND 100 AND project_name = btrim(project_name) AND project_name !~ '^[[:space:]]*$' AND project_name !~ '[[:cntrl:]]'), \n\tCONSTRAINT ck_crews_description_valid CHECK (description IS NULL OR char_length(description) BETWEEN 1 AND 1000), \n\tCONSTRAINT fk_crews_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE role_permissions (\n\trole_code VARCHAR(32) NOT NULL, \n\tpermission_code VARCHAR(100) NOT NULL, \n\tCONSTRAINT pk_role_permissions PRIMARY KEY (role_code, permission_code), \n\tCONSTRAINT fk_role_permissions_role_code_roles FOREIGN KEY(role_code) REFERENCES roles (code) ON DELETE RESTRICT, \n\tCONSTRAINT fk_role_permissions_permission_code_permissions FOREIGN KEY(permission_code) REFERENCES permissions (code) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE audit_logs (\n\tid UUID NOT NULL, \n\tcrew_id UUID, \n\tactor_type VARCHAR(20) NOT NULL, \n\tactor_id UUID, \n\taction VARCHAR(100) NOT NULL, \n\ttarget_type VARCHAR(50) NOT NULL, \n\ttarget_id UUID, \n\tbefore_summary JSONB, \n\tafter_summary JSONB, \n\tresult VARCHAR(20) NOT NULL, \n\treason_code VARCHAR(100), \n\trequest_id VARCHAR(32), \n\trun_id UUID, \n\tproposal_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_audit_logs PRIMARY KEY (id), \n\tCONSTRAINT ck_audit_logs_actor_type_valid CHECK (actor_type IN ('user', 'system')), \n\tCONSTRAINT ck_audit_logs_result_valid CHECK (result IN ('success', 'denied', 'failed')), \n\tCONSTRAINT ck_audit_logs_user_actor_required CHECK (actor_type <> 'user' OR actor_id IS NOT NULL), \n\tCONSTRAINT fk_audit_logs_crew_id_crews FOREIGN KEY(crew_id) REFERENCES crews (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_audit_logs_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE crew_members (\n\tid UUID NOT NULL, \n\tcrew_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tdisplay_name VARCHAR(30) NOT NULL, \n\tstatus VARCHAR(20) DEFAULT 'active' NOT NULL, \n\tjoined_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tended_at TIMESTAMP WITH TIME ZONE, \n\tlock_version INTEGER DEFAULT 1 NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_crew_members PRIMARY KEY (id), \n\tCONSTRAINT uq_crew_members_crew_user UNIQUE (crew_id, user_id), \n\tCONSTRAINT uq_crew_members_crew_id_id UNIQUE (crew_id, id), \n\tCONSTRAINT ck_crew_members_status_valid CHECK (status IN ('active', 'left', 'removed')), \n\tCONSTRAINT ck_crew_members_ended_state_pair CHECK ((status = 'active' AND ended_at IS NULL) OR (status <> 'active' AND ended_at IS NOT NULL)), \n\tCONSTRAINT ck_crew_members_lock_version_positive CHECK (lock_version >= 1), \n\tCONSTRAINT ck_crew_members_display_name_valid CHECK (char_length(display_name) BETWEEN 1 AND 30 AND display_name = btrim(display_name) AND display_name !~ '^[[:space:]]*$' AND display_name !~ '[[:cntrl:]]'), \n\tCONSTRAINT fk_crew_members_crew_id_crews FOREIGN KEY(crew_id) REFERENCES crews (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_crew_members_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE member_permission_overrides (\n\tcrew_id UUID NOT NULL, \n\tmember_id UUID NOT NULL, \n\tpermission_code VARCHAR(100) NOT NULL, \n\teffect VARCHAR(5) NOT NULL, \n\tCONSTRAINT pk_member_permission_overrides PRIMARY KEY (crew_id, member_id, permission_code), \n\tCONSTRAINT fk_member_permission_overrides_crew_id_crew_members FOREIGN KEY(crew_id, member_id) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_member_permission_overrides_effect_valid CHECK (effect IN ('allow', 'deny')), \n\tCONSTRAINT ck_member_permission_overrides_organization_override_forbidden CHECK (permission_code NOT IN ('member.invite', 'member.remove', 'member.authorize')), \n\tCONSTRAINT fk_member_permission_overrides_permission_code_permissions FOREIGN KEY(permission_code) REFERENCES permissions (code) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE member_roles (\n\tcrew_id UUID NOT NULL, \n\tmember_id UUID NOT NULL, \n\trole_code VARCHAR(32) NOT NULL, \n\tCONSTRAINT pk_member_roles PRIMARY KEY (crew_id, member_id, role_code), \n\tCONSTRAINT fk_member_roles_crew_id_crew_members FOREIGN KEY(crew_id, member_id) REFERENCES crew_members (crew_id, id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_member_roles_role_code_roles FOREIGN KEY(role_code) REFERENCES roles (code) ON DELETE RESTRICT\n)\n\n",
    "CREATE INDEX ix_audit_logs_crew_id ON audit_logs (crew_id)",
    "CREATE INDEX ix_crew_members_user_id ON crew_members (user_id)"
]
ROLES = [
    {
        "code": "admin",
        "name": "管理员"
    },
    {
        "code": "director",
        "name": "导演"
    },
    {
        "code": "producer",
        "name": "制片"
    },
    {
        "code": "writer",
        "name": "编剧"
    },
    {
        "code": "cinematographer",
        "name": "摄影"
    },
    {
        "code": "art",
        "name": "美术"
    },
    {
        "code": "staff",
        "name": "工作人员"
    },
    {
        "code": "actor",
        "name": "演员"
    }
]
PERMISSIONS = [
    {
        "code": "availability.read_all",
        "description": "availability.read_all"
    },
    {
        "code": "availability.self",
        "description": "availability.self"
    },
    {
        "code": "call_sheet.edit",
        "description": "call_sheet.edit"
    },
    {
        "code": "call_sheet.export",
        "description": "call_sheet.export"
    },
    {
        "code": "call_sheet.publish",
        "description": "call_sheet.publish"
    },
    {
        "code": "call_sheet.read_related",
        "description": "call_sheet.read_related"
    },
    {
        "code": "call_sheet.review",
        "description": "call_sheet.review"
    },
    {
        "code": "crew.read",
        "description": "crew.read"
    },
    {
        "code": "diane.use",
        "description": "diane.use"
    },
    {
        "code": "member.authorize",
        "description": "member.authorize"
    },
    {
        "code": "member.invite",
        "description": "member.invite"
    },
    {
        "code": "member.read",
        "description": "member.read"
    },
    {
        "code": "member.remove",
        "description": "member.remove"
    },
    {
        "code": "notification.acknowledge_self",
        "description": "notification.acknowledge_self"
    },
    {
        "code": "notification.read_self",
        "description": "notification.read_self"
    },
    {
        "code": "notification.receipts",
        "description": "notification.receipts"
    },
    {
        "code": "plan.edit",
        "description": "plan.edit"
    },
    {
        "code": "plan.publish",
        "description": "plan.publish"
    },
    {
        "code": "plan.read_all",
        "description": "plan.read_all"
    },
    {
        "code": "plan.read_related",
        "description": "plan.read_related"
    },
    {
        "code": "reservation.apply",
        "description": "reservation.apply"
    },
    {
        "code": "reservation.review",
        "description": "reservation.review"
    },
    {
        "code": "resource.checkout",
        "description": "resource.checkout"
    },
    {
        "code": "resource.manage",
        "description": "resource.manage"
    },
    {
        "code": "resource.read",
        "description": "resource.read"
    },
    {
        "code": "resource.return",
        "description": "resource.return"
    },
    {
        "code": "scene.edit",
        "description": "scene.edit"
    },
    {
        "code": "scene.read",
        "description": "scene.read"
    },
    {
        "code": "scene.review",
        "description": "scene.review"
    },
    {
        "code": "script.confirm",
        "description": "script.confirm"
    },
    {
        "code": "script.edit",
        "description": "script.edit"
    },
    {
        "code": "script.read",
        "description": "script.read"
    },
    {
        "code": "stock.manage",
        "description": "stock.manage"
    },
    {
        "code": "storyboard.edit",
        "description": "storyboard.edit"
    },
    {
        "code": "storyboard.read",
        "description": "storyboard.read"
    },
    {
        "code": "storyboard.review",
        "description": "storyboard.review"
    },
    {
        "code": "task.manage",
        "description": "task.manage"
    },
    {
        "code": "task.read",
        "description": "task.read"
    },
    {
        "code": "task.review",
        "description": "task.review"
    },
    {
        "code": "task.work",
        "description": "task.work"
    }
]
ROLE_PERMISSIONS = [
    {
        "role_code": "admin",
        "permission_code": "member.authorize"
    },
    {
        "role_code": "admin",
        "permission_code": "member.invite"
    },
    {
        "role_code": "admin",
        "permission_code": "member.remove"
    },
    {
        "role_code": "director",
        "permission_code": "availability.read_all"
    },
    {
        "role_code": "director",
        "permission_code": "plan.read_all"
    },
    {
        "role_code": "director",
        "permission_code": "scene.edit"
    },
    {
        "role_code": "director",
        "permission_code": "scene.read"
    },
    {
        "role_code": "director",
        "permission_code": "scene.review"
    },
    {
        "role_code": "director",
        "permission_code": "script.confirm"
    },
    {
        "role_code": "director",
        "permission_code": "script.edit"
    },
    {
        "role_code": "director",
        "permission_code": "script.read"
    },
    {
        "role_code": "director",
        "permission_code": "storyboard.edit"
    },
    {
        "role_code": "director",
        "permission_code": "storyboard.read"
    },
    {
        "role_code": "director",
        "permission_code": "storyboard.review"
    },
    {
        "role_code": "director",
        "permission_code": "task.manage"
    },
    {
        "role_code": "producer",
        "permission_code": "availability.read_all"
    },
    {
        "role_code": "producer",
        "permission_code": "call_sheet.edit"
    },
    {
        "role_code": "producer",
        "permission_code": "call_sheet.export"
    },
    {
        "role_code": "producer",
        "permission_code": "call_sheet.publish"
    },
    {
        "role_code": "producer",
        "permission_code": "call_sheet.review"
    },
    {
        "role_code": "producer",
        "permission_code": "plan.edit"
    },
    {
        "role_code": "producer",
        "permission_code": "plan.publish"
    },
    {
        "role_code": "producer",
        "permission_code": "plan.read_all"
    },
    {
        "role_code": "producer",
        "permission_code": "resource.manage"
    },
    {
        "role_code": "producer",
        "permission_code": "task.manage"
    },
    {
        "role_code": "writer",
        "permission_code": "scene.edit"
    },
    {
        "role_code": "writer",
        "permission_code": "scene.read"
    },
    {
        "role_code": "writer",
        "permission_code": "script.confirm"
    },
    {
        "role_code": "writer",
        "permission_code": "script.edit"
    },
    {
        "role_code": "writer",
        "permission_code": "script.read"
    },
    {
        "role_code": "writer",
        "permission_code": "storyboard.edit"
    },
    {
        "role_code": "writer",
        "permission_code": "storyboard.read"
    }
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(sa.DDL(statement))
    op.bulk_insert(sa.table("roles", sa.column("code", sa.String), sa.column("name", sa.String)), ROLES)
    op.bulk_insert(sa.table("permissions", sa.column("code", sa.String), sa.column("description", sa.String)), PERMISSIONS)
    op.bulk_insert(sa.table("role_permissions", sa.column("role_code", sa.String), sa.column("permission_code", sa.String)), ROLE_PERMISSIONS)


def downgrade():
    op.drop_table("member_roles")
    op.drop_table("member_permission_overrides")
    op.drop_table("crew_members")
    op.drop_table("audit_logs")
    op.drop_table("role_permissions")
    op.drop_table("crews")
    op.drop_table("roles")
    op.drop_table("permissions")

