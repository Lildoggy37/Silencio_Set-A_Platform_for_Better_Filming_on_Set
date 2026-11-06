"""Private Diane conversations, turns and explicit preferences."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0027_diane_memory'
down_revision = '0026_task_proposals'
branch_labels = depends_on = None


def scope_columns():
    return [sa.Column('crew_id', sa.Uuid(), sa.ForeignKey('crews.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('member_id', sa.Uuid(), sa.ForeignKey('crew_members.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('membership_joined_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table('diane_conversations', sa.Column('id', sa.Uuid(), primary_key=True), *scope_columns(),
        sa.Column('title', sa.String(80), nullable=False), sa.Column('next_ordinal', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_diane_conversations_crew_id', 'diane_conversations', ['crew_id'])
    op.create_index('ix_diane_conversations_user_id', 'diane_conversations', ['user_id'])
    op.create_table('diane_conversation_turns', sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('diane_conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False), sa.Column('question', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False), sa.Column('result', JSONB()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('conversation_id', 'ordinal'),
        sa.CheckConstraint("status IN ('running','completed','failed','interrupted')", name=op.f('ck_diane_conversation_turns_status_valid')),
        sa.CheckConstraint('ordinal >= 1', name=op.f('ck_diane_conversation_turns_ordinal_positive')))
    op.create_index('ix_diane_conversation_turns_conversation_id', 'diane_conversation_turns', ['conversation_id'])
    op.create_table('diane_preferences', sa.Column('id', sa.Uuid(), primary_key=True), *scope_columns(),
        sa.Column('response_style', sa.String(20), nullable=False), sa.Column('focus', sa.String(20), nullable=False),
        sa.UniqueConstraint('crew_id', 'user_id', 'member_id', 'membership_joined_at'),
        sa.CheckConstraint("response_style IN ('concise','detailed')", name=op.f('ck_diane_preferences_style_valid')),
        sa.CheckConstraint("focus IN ('general','schedule','resources')", name=op.f('ck_diane_preferences_focus_valid')))


def downgrade():
    op.drop_table('diane_preferences')
    op.drop_table('diane_conversation_turns')
    op.drop_table('diane_conversations')
