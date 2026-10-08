"""channel identity avatar + profile refresh time

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('channel_identities', sa.Column('avatar_url', sa.Text(), nullable=True))
    op.add_column('channel_identities', sa.Column('profile_fetched_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_messages_created_at', 'messages', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_messages_created_at', table_name='messages')
    op.drop_column('channel_identities', 'profile_fetched_at')
    op.drop_column('channel_identities', 'avatar_url')
