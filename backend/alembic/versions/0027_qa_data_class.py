"""classify Q&A as fixed or variable data

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("qa_pairs", sa.Column("data_class", sa.String(length=16), nullable=False, server_default="fixed"))
    op.create_index("ix_qa_pairs_data_class", "qa_pairs", ["data_class"])


def downgrade() -> None:
    op.drop_index("ix_qa_pairs_data_class", table_name="qa_pairs")
    op.drop_column("qa_pairs", "data_class")
