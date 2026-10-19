"""track the embedding configuration used for every vector

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("chunks", "qa_pairs", "semantic_cache"):
        op.add_column(table, sa.Column("embedding_version", sa.String(length=255), nullable=True))
        op.create_index(f"ix_{table}_embedding_version", table, ["embedding_version"])


def downgrade():
    for table in ("semantic_cache", "qa_pairs", "chunks"):
        op.drop_index(f"ix_{table}_embedding_version", table_name=table)
        op.drop_column(table, "embedding_version")
