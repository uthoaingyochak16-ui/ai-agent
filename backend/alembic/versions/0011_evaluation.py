"""answer quality evaluation tables

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("eval_cases",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=False), sa.Column("expected_path", sa.String(32)),
        sa.Column("language", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_eval_cases_language", "eval_cases", ["language"])
    op.create_index("ix_eval_cases_active", "eval_cases", ["active"])
    op.create_table("eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("settings_snapshot", postgresql.JSONB(), nullable=False), sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed", sa.Integer(), nullable=False, server_default="0"), sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_index("ix_eval_runs_status", "eval_runs", ["status"])
    op.create_table("eval_results",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("run_id", sa.Integer(), sa.ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("eval_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actual_answer", sa.Text(), nullable=False), sa.Column("path", sa.String(32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"), sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("judge_cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("retrieved_chunk_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("correct", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("grounded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("missed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "case_id", name="uq_eval_run_case"))
    op.create_index("ix_eval_results_run_id", "eval_results", ["run_id"])
    op.create_index("ix_eval_results_case_id", "eval_results", ["case_id"])


def downgrade():
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
    op.drop_table("eval_cases")
