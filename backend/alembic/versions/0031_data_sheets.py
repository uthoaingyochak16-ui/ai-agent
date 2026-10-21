"""data sheets: spreadsheet-like tables of facts (books, branches ...) the AI searches (services/sheets.py)

Revision ID: 0031
Revises: 0030
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_sheets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text),
        sa.Column("columns", JSONB, nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "data_rows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sheet_id", sa.Integer, sa.ForeignKey("data_sheets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("values", JSONB, nullable=False, server_default="{}"),
        sa.Column("search_text", sa.Text, nullable=False, server_default=""),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_data_rows_sheet_id", "data_rows", ["sheet_id"])


def downgrade() -> None:
    op.drop_table("data_rows")
    op.drop_table("data_sheets")
    op.execute("DELETE FROM keyword_entries WHERE source_type = 'sheetrow'")
