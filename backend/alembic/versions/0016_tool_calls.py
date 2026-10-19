"""tool calling: tool_calls log + a conversation's tool call waiting for the guest's "yes"

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('tool_calls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=True),
    sa.Column('tool', sa.String(length=64), nullable=False),
    sa.Column('arguments', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('result', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tool_calls_conversation_id', 'tool_calls', ['conversation_id'])
    op.add_column('conversations', sa.Column('pending_tool', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('conversations', sa.Column('pending_tool_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('conversations', 'pending_tool_at')
    op.drop_column('conversations', 'pending_tool')
    op.drop_index('ix_tool_calls_conversation_id', table_name='tool_calls')
    op.drop_table('tool_calls')
