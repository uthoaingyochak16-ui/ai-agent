"""keyword index: one row per (keyword stem, source record) over chunks, Q&A, variables and programs

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chunks', sa.Column('keywords', sa.Text(), nullable=True))   # topic / keywords given in the file itself
    op.create_table('keyword_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stem', sa.String(length=64), nullable=False),
    sa.Column('term', sa.String(length=64), nullable=False),
    sa.Column('source_type', sa.String(length=12), nullable=False),
    sa.Column('source_ref', sa.String(length=160), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=True),
    sa.Column('weight', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_type', 'source_ref', 'stem', name='uq_kw_source_stem')
    )
    op.create_index('ix_kw_stem', 'keyword_entries', ['stem'])
    op.create_index('ix_kw_source', 'keyword_entries', ['source_type', 'source_ref'])
    op.create_index('ix_kw_document', 'keyword_entries', ['document_id'])


def downgrade() -> None:
    op.drop_index('ix_kw_document', table_name='keyword_entries')
    op.drop_index('ix_kw_source', table_name='keyword_entries')
    op.drop_index('ix_kw_stem', table_name='keyword_entries')
    op.drop_table('keyword_entries')
    op.drop_column('chunks', 'keywords')
