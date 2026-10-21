"""social posts: posts written / scheduled here and posts read from the Pages and Instagram accounts, with their
performance (services/posts.py)

Revision ID: 0033
Revises: 0032
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "social_posts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(160)),
        sa.Column("source", sa.String(12), nullable=False, server_default="app"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("message", sa.Text, nullable=False, server_default=""),
        sa.Column("link", sa.Text),
        sa.Column("image_url", sa.Text),
        sa.Column("media_type", sa.String(24)),
        sa.Column("permalink", sa.Text),
        sa.Column("topic", sa.String(60)),
        sa.Column("ai_written", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("metrics", JSONB, nullable=False, server_default="{}"),
        sa.Column("metrics_at", sa.DateTime(timezone=True)),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text),
        sa.Column("created_by", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("channel", "external_id", name="uq_post_channel_external"),
    )
    op.create_index("ix_social_posts_status_when", "social_posts", ["status", "scheduled_at"])


def downgrade() -> None:
    op.drop_table("social_posts")
