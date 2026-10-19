"""indexed trigram candidates for Q&A wording shortcuts

Revision ID: 0014
Revises: 0013
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index("ix_qa_pairs_question_norm_trgm", "qa_pairs", ["question_norm"], unique=False,
                    postgresql_using="gin", postgresql_ops={"question_norm": "gin_trgm_ops"})


def downgrade():
    op.drop_index("ix_qa_pairs_question_norm_trgm", table_name="qa_pairs")
