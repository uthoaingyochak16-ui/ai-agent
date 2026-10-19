"""semantic cache status (auto | verified | blocked)

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('semantic_cache', sa.Column('status', sa.String(length=12), nullable=False, server_default='auto'))
    op.add_column('semantic_cache', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_cache_status', 'semantic_cache', ['status'])


def downgrade() -> None:
    op.drop_index('ix_cache_status', table_name='semantic_cache')
    op.drop_column('semantic_cache', 'updated_at')
    op.drop_column('semantic_cache', 'status')
