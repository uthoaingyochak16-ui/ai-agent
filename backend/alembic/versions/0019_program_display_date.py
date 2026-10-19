"""Preserve staff-entered readable program dates.

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("programs", sa.Column("date_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("programs", "date_text")