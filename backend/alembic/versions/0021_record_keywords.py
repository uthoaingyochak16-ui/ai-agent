"""keyword origin + per-record keyword edits (added by hand / by the AI / by the uniqueness check, or removed)

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('keyword_entries', sa.Column('origin', sa.String(length=12), nullable=False, server_default='body'))
    op.create_table('record_keywords',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_type', sa.String(length=12), nullable=False),
    sa.Column('source_ref', sa.String(length=160), nullable=False),
    sa.Column('added', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
    sa.Column('removed', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
    sa.Column('ai_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_type', 'source_ref', name='uq_record_keywords_source')
    )


def downgrade() -> None:
    op.drop_table('record_keywords')
    op.drop_column('keyword_entries', 'origin')
