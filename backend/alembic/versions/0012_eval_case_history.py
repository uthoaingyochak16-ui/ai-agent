"""evaluation follow-up context

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("eval_cases", sa.Column("history", postgresql.JSONB(), nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("eval_cases", "history")
