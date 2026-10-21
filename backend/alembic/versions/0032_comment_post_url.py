"""post comments: the post's link (Instagram permalinks cannot be built from ids)

Revision ID: 0032
Revises: 0031
"""
from alembic import op
import sqlalchemy as sa


revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("post_comments", sa.Column("post_url", sa.Text))
    op.create_index("ix_post_comments_channel_page", "post_comments", ["channel", "page_id"])


def downgrade() -> None:
    op.drop_index("ix_post_comments_channel_page", "post_comments")
    op.drop_column("post_comments", "post_url")
