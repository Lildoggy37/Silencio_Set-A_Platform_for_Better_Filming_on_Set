"""Working-script epochs and immutable manual source applicability reviews."""
from alembic import op
import sqlalchemy as sa

revision = '0023_source_reviews'
down_revision = '0022_plan_withdrawal'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('scripts', sa.Column('working_revision', sa.Integer(), server_default='0', nullable=False))
    op.execute('UPDATE scripts SET working_revision = 1 WHERE current_version_id IS NOT NULL')
    op.create_check_constraint(op.f('ck_scripts_working_revision_valid'), 'scripts',
        'working_revision BETWEEN 0 AND 2147483647 AND ((current_version_id IS NULL AND working_revision = 0) OR (current_version_id IS NOT NULL AND working_revision > 0))')
    op.create_table('source_review_records',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('crew_id', sa.Uuid(), nullable=False),
        sa.Column('breakdown_id', sa.Uuid()),
        sa.Column('storyboard_version_id', sa.Uuid()),
        sa.Column('script_id', sa.Uuid(), nullable=False),
        sa.Column('source_script_version_id', sa.Uuid(), nullable=False),
        sa.Column('working_script_version_id', sa.Uuid(), nullable=False),
        sa.Column('working_revision', sa.Integer(), nullable=False),
        sa.Column('target_lock_version', sa.Integer(), nullable=False),
        sa.Column('reviewed_by', sa.Uuid(), nullable=False),
        sa.Column('reviewer_joined_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('conclusion', sa.String(20), nullable=False),
        sa.Column('review_note', sa.String(1000), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['crew_id','breakdown_id'], ['scene_breakdowns.crew_id','scene_breakdowns.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['crew_id','storyboard_version_id'], ['storyboard_versions.crew_id','storyboard_versions.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['crew_id','script_id','source_script_version_id'], ['script_versions.crew_id','script_versions.script_id','script_versions.id'], name='fk_source_review_source_script_version_id', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['crew_id','script_id','working_script_version_id'], ['script_versions.crew_id','script_versions.script_id','script_versions.id'], name='fk_source_review_working_script_version_id', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['crew_id','reviewed_by'], ['crew_members.crew_id','crew_members.id'], name='fk_source_review_member', ondelete='RESTRICT'),
        sa.CheckConstraint('(breakdown_id IS NULL) <> (storyboard_version_id IS NULL)', name=op.f('ck_source_review_records_one_target')),
        sa.CheckConstraint("conclusion IN ('still_applicable','needs_revision') AND char_length(review_note) BETWEEN 1 AND 1000", name=op.f('ck_source_review_records_conclusion_valid')),
        sa.CheckConstraint('target_lock_version > 0 AND working_revision > 0 AND source_script_version_id <> working_script_version_id', name=op.f('ck_source_review_records_basis_valid')),
    )
    for kind, column in [('breakdown','breakdown_id'), ('storyboard','storyboard_version_id')]:
        op.create_index(f'ix_source_reviews_{kind}', 'source_review_records', ['crew_id',column,'working_revision','target_lock_version','reviewed_at','id'])


def downgrade():
    op.drop_table('source_review_records')
    op.drop_constraint(op.f('ck_scripts_working_revision_valid'), 'scripts', type_='check')
    op.drop_column('scripts','working_revision')
