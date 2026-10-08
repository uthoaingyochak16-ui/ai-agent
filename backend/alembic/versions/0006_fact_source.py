"""profile fact source (ai | staff)

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('profile_facts', sa.Column('source', sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column('profile_facts', 'source')
