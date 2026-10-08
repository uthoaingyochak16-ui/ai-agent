"""conversation pause time (auto-resume) + chunk question (structured Q&A files)

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('ai_paused_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('chunks', sa.Column('question', sa.Text(), nullable=True))
    op.execute("UPDATE conversations SET ai_paused_at = now() WHERE ai_paused")


def downgrade() -> None:
    op.drop_column('chunks', 'question')
    op.drop_column('conversations', 'ai_paused_at')
