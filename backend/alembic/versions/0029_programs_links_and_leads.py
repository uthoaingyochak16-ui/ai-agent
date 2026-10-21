"""program registration links from the main application; the lead list

programs: registration_url (the form a guest fills in), source + external_id (rows kept in step with the main app).
leads: guests interested in a program or in knowing more; staff send them to the main app as a guest + task.

Revision ID: 0029
Revises: 0028
"""
from alembic import op
import sqlalchemy as sa


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("programs", sa.Column("registration_url", sa.String(500)))
    op.add_column("programs", sa.Column("source", sa.String(32)))
    op.add_column("programs", sa.Column("external_id", sa.String(64)))
    op.create_index("ix_programs_source_external", "programs", ["source", "external_id"], unique=True)
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("profile_id", sa.Integer, sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("channel", sa.String(16)),
        sa.Column("interest", sa.String(300), nullable=False),
        sa.Column("program_id", sa.Integer, sa.ForeignKey("programs.id", ondelete="SET NULL")),
        sa.Column("note", sa.Text),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("main_app_guest_id", sa.String(64)),
        sa.Column("main_app_task_id", sa.String(64)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_leads_profile_id", "leads", ["profile_id"])
    op.create_index("ix_leads_status", "leads", ["status"])


def downgrade() -> None:
    op.drop_table("leads")
    op.drop_index("ix_programs_source_external", "programs")
    for col in ("external_id", "source", "registration_url"):
        op.drop_column("programs", col)
