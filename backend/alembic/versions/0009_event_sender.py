"""webhook event sender (debounce: find a guest's newer / pending messages)

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('webhook_events', sa.Column('sender_id', sa.String(length=128), nullable=True))
    op.execute("UPDATE webhook_events SET sender_id = payload->>'external_user_id'")
    op.create_index('ix_events_sender', 'webhook_events', ['channel', 'sender_id', 'id'])


def downgrade() -> None:
    op.drop_index('ix_events_sender', table_name='webhook_events')
    op.drop_column('webhook_events', 'sender_id')
