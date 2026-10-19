"""lead capture: how often a guest was asked for name/phone/location, and the question held while asking

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa


revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('profiles', sa.Column('lead_asks', sa.SmallInteger(), nullable=False, server_default='0'))
    op.add_column('conversations', sa.Column('lead_awaiting_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('conversations', sa.Column('lead_pending_question', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversations', 'lead_pending_question')
    op.drop_column('conversations', 'lead_awaiting_at')
    op.drop_column('profiles', 'lead_asks')
