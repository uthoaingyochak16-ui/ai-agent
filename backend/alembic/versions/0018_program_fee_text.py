"""Allow program fee and registration comments to be stored together.

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("programs", "fee", existing_type=sa.String(length=120), type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    op.alter_column("programs", "fee", existing_type=sa.Text(), type_=sa.String(length=120), existing_nullable=True)