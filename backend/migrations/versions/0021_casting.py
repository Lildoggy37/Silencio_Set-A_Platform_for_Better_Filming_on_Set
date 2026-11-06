"""Script-scoped character casting with membership epochs."""
from alembic import op
import sqlalchemy as sa

revision = '0021_casting'
down_revision = '0020_locations'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('character_castings',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('crew_id', sa.Uuid(), nullable=False),
        sa.Column('script_id', sa.Uuid(), nullable=False),
        sa.Column('character_name', sa.String(100), nullable=False),
        sa.Column('member_id', sa.Uuid(), nullable=False),
        sa.Column('membership_joined_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('display_name', sa.String(30), nullable=False),
        sa.Column('updated_by', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('script_id', 'character_name', name='uq_casting_character'),
        sa.ForeignKeyConstraint(['crew_id', 'script_id'], ['scripts.crew_id', 'scripts.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['crew_id', 'member_id'], ['crew_members.crew_id', 'crew_members.id'], name='fk_casting_member_id', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['crew_id', 'updated_by'], ['crew_members.crew_id', 'crew_members.id'], name='fk_casting_updated_by', ondelete='RESTRICT'),
        sa.CheckConstraint('char_length(character_name) BETWEEN 1 AND 100', name=op.f('ck_character_castings_name_valid')),
    )


def downgrade():
    op.drop_table('character_castings')
