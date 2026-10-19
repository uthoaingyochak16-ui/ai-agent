"""recompute qa_pairs.question_norm: the normalizer now keeps Bangla vowel signs

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa


revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.rag.templates import normalize_question
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, question FROM qa_pairs")).all()
    for qid, question in rows:
        conn.execute(sa.text("UPDATE qa_pairs SET question_norm = :n WHERE id = :i"),
                     {"n": normalize_question(question), "i": qid})


def downgrade() -> None:
    pass  # the old form is a lossy version of the new one; nothing to restore
