"""program district follow-up: asked at most once per conversation

Additive only (two nullable / defaulted columns); downgrade drops just these two.

Revision ID: 0026
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa


revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('district_asked_at', sa.DateTime(timezone=True)))
    op.add_column('conversations', sa.Column('district_awaiting', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('conversations', 'district_awaiting')
    op.drop_column('conversations', 'district_asked_at')
