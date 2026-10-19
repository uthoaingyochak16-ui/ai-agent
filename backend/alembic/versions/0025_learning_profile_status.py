"""guest profile request status, Unknown-questions approval queue, standard Q&A categories

Additive only: no table or row is dropped. Old Q&A categories are copied to qa_category_backup first, so downgrade
puts every one back exactly.

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.rag.categories import migrate_category
    from app.rag.templates import normalize_question
    conn = op.get_bind()

    # ---- profiles: where the name/phone request stands --------------------------------------------------------------
    op.add_column('profiles', sa.Column('personal_info_request_status', sa.String(12), nullable=False,
                                        server_default='not_asked'))
    conn.execute(sa.text("""
        UPDATE profiles SET personal_info_request_status = CASE
            WHEN name IS NOT NULL AND phone_hash IS NOT NULL THEN 'provided'
            WHEN lead_asks >= 2 THEN 'deferred'      -- asked and reminded: never again
            WHEN lead_asks = 1 THEN 'asked'
            WHEN name IS NOT NULL OR phone_hash IS NOT NULL THEN 'provided'
            ELSE 'not_asked' END"""))

    # ---- unanswered_questions -> the approval queue -----------------------------------------------------------------
    op.add_column('unanswered_questions', sa.Column('question_norm', sa.Text()))
    op.add_column('unanswered_questions', sa.Column('status', sa.String(12), nullable=False, server_default='new'))
    op.add_column('unanswered_questions', sa.Column('occurrences', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('unanswered_questions', sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.add_column('unanswered_questions', sa.Column('suggested_category', sa.String(64)))
    op.add_column('unanswered_questions', sa.Column('suggested_keywords', postgresql.JSONB()))
    op.add_column('unanswered_questions', sa.Column('draft_answer', sa.Text()))
    op.add_column('unanswered_questions', sa.Column('wrong_answer', sa.Text()))
    op.create_index('ix_unanswered_questions_question_norm', 'unanswered_questions', ['question_norm'])
    op.create_index('ix_unanswered_questions_status', 'unanswered_questions', ['status'])
    conn.execute(sa.text("UPDATE unanswered_questions SET last_seen_at = created_at, "
                         "status = CASE WHEN resolved THEN 'approved' ELSE 'new' END"))
    for uid, q in conn.execute(sa.text("SELECT id, question FROM unanswered_questions")).all():
        conn.execute(sa.text("UPDATE unanswered_questions SET question_norm = :n WHERE id = :i"),
                     {"n": normalize_question(q or ""), "i": uid})
    # identical open questions -> one row with the summed count (the extra rows are kept, marked ignored)
    for norm, ids in conn.execute(sa.text(
            "SELECT question_norm, array_agg(id ORDER BY id) FROM unanswered_questions WHERE status = 'new' "
            "GROUP BY question_norm HAVING count(*) > 1")).all():
        keep, rest = ids[0], ids[1:]
        conn.execute(sa.text("UPDATE unanswered_questions SET occurrences = :n, last_seen_at = "
                             "(SELECT max(created_at) FROM unanswered_questions WHERE id = ANY(:all)) WHERE id = :k"),
                     {"n": len(ids), "all": ids, "k": keep})
        conn.execute(sa.text("UPDATE unanswered_questions SET status = 'ignored', resolved = true WHERE id = ANY(:r)"),
                     {"r": rest})

    # ---- Q&A categories -> the standard list (backup first) ---------------------------------------------------------
    op.create_table('qa_category_backup',
                    sa.Column('qa_id', sa.Integer(), primary_key=True),
                    sa.Column('category', sa.String(64)),
                    sa.Column('saved_at', sa.DateTime(timezone=True), server_default=sa.func.now()))
    conn.execute(sa.text("INSERT INTO qa_category_backup (qa_id, category) SELECT id, category FROM qa_pairs"))
    for qid, cat, question, answer in conn.execute(sa.text(
            "SELECT id, category, question, answer_template FROM qa_pairs")).all():
        new = migrate_category(cat, question, answer)
        if new != cat:
            conn.execute(sa.text("UPDATE qa_pairs SET category = :c WHERE id = :i"), {"c": new, "i": qid})
    # cached answers and the keyword index carry the category: let both refresh
    conn.execute(sa.text("UPDATE app_settings SET value = to_jsonb((value #>> '{}')::int + 1) WHERE key = 'context_version'"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE qa_pairs q SET category = b.category FROM qa_category_backup b WHERE b.qa_id = q.id"))
    op.drop_table('qa_category_backup')
    op.drop_index('ix_unanswered_questions_status', table_name='unanswered_questions')
    op.drop_index('ix_unanswered_questions_question_norm', table_name='unanswered_questions')
    conn.execute(sa.text("UPDATE unanswered_questions SET resolved = status IN ('approved', 'ignored')"))
    for col in ('wrong_answer', 'draft_answer', 'suggested_keywords', 'suggested_category', 'last_seen_at',
                'occurrences', 'status', 'question_norm'):
        op.drop_column('unanswered_questions', col)
    op.drop_column('profiles', 'personal_info_request_status')
