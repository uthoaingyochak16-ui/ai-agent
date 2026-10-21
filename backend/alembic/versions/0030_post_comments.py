"""comments on the Facebook Page's posts: the AI replies, likes, and hides / reports spam and attacks (services/comments.py)

Revision ID: 0030
Revises: 0029
"""
from alembic import op
import sqlalchemy as sa


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post_comments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("channel", sa.String(16), nullable=False, server_default="facebook"),
        sa.Column("page_id", sa.String(64), nullable=False),
        sa.Column("post_id", sa.String(160), nullable=False),
        sa.Column("comment_id", sa.String(160), nullable=False, unique=True),
        sa.Column("parent_id", sa.String(160)),
        sa.Column("from_id", sa.String(64)),
        sa.Column("from_name", sa.String(160)),
        sa.Column("message", sa.Text, nullable=False, server_default=""),
        sa.Column("post_text", sa.Text),
        sa.Column("source", sa.String(12), nullable=False, server_default="webhook"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("kind", sa.String(24)),
        sa.Column("reason", sa.Text),
        sa.Column("confidence", sa.Float),
        sa.Column("action", sa.String(12)),
        sa.Column("reply_text", sa.Text),
        sa.Column("reply_path", sa.String(32)),
        sa.Column("reply_comment_id", sa.String(160)),
        sa.Column("liked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("private_replied", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("replied_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_post_comments_status", "post_comments", ["status", "received_at"])
    op.create_index("ix_post_comments_post_id", "post_comments", ["post_id"])
    op.create_index("ix_post_comments_reply_comment_id", "post_comments", ["reply_comment_id"])


def downgrade() -> None:
    op.drop_table("post_comments")
