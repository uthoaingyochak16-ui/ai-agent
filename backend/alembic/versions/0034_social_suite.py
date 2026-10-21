"""social suite: post approval / history / A-B tests / program and AI-plan drafts; daily audience numbers; weekly
reports; WhatsApp broadcasts; ad drafts

Revision ID: 0034
Revises: 0033
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in (sa.Column("program_id", sa.Integer), sa.Column("suggested_at", sa.DateTime(timezone=True)),
                sa.Column("note", sa.Text), sa.Column("ab_group", sa.String(32)), sa.Column("ab_variant", sa.String(4)),
                sa.Column("history", JSONB, nullable=False, server_default="[]")):
        op.add_column("social_posts", col)
    op.create_index("ix_social_posts_program_id", "social_posts", ["program_id"])
    op.create_index("ix_social_posts_ab_group", "social_posts", ["ab_group"])
    op.create_table(
        "social_account_stats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("followers", sa.Integer),
        sa.Column("metrics", JSONB, nullable=False, server_default="{}"),
        sa.UniqueConstraint("day", "channel", "account_id", name="uq_account_stat_day"),
    )
    op.create_table(
        "social_reports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("data", JSONB, nullable=False, server_default="{}"),
        sa.Column("sent", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("template", sa.String(120), nullable=False),
        sa.Column("language", sa.String(16), nullable=False, server_default="bn"),
        sa.Column("params", JSONB, nullable=False, server_default="[]"),
        sa.Column("preview", sa.Text),
        sa.Column("audience", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("created_by", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "broadcast_recipients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("broadcast_id", sa.Integer, sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("identity_id", sa.Integer, sa.ForeignKey("channel_identities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("broadcast_id", "identity_id", name="uq_broadcast_identity"),
    )
    op.create_index("ix_broadcast_recipients_broadcast_id", "broadcast_recipients", ["broadcast_id"])
    op.create_table(
        "ad_drafts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("objective", sa.String(40), nullable=False, server_default="engagement"),
        sa.Column("post_id", sa.Integer, sa.ForeignKey("social_posts.id", ondelete="SET NULL")),
        sa.Column("program_id", sa.Integer),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("audience", JSONB, nullable=False, server_default="{}"),
        sa.Column("daily_budget", sa.Numeric(12, 2)),
        sa.Column("days", sa.Integer),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("note", sa.Text),
        sa.Column("created_by", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    for t in ("ad_drafts", "broadcast_recipients", "broadcasts", "social_reports", "social_account_stats"):
        op.drop_table(t)
    op.drop_index("ix_social_posts_ab_group", "social_posts")
    op.drop_index("ix_social_posts_program_id", "social_posts")
    for c in ("history", "ab_variant", "ab_group", "note", "suggested_at", "program_id"):
        op.drop_column("social_posts", c)
