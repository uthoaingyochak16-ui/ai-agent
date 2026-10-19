"""several Facebook Pages / Instagram accounts / WhatsApp numbers per channel

channel_configs: one row per connected account (UNIQUE(channel) -> UNIQUE(channel, account_id)) and a staff label.
channel_identities: which account the guest wrote to, so replies go out through the same Page / number.
Existing rows are kept as they are: each channel's current account simply becomes its first one.

Revision ID: 0028
Revises: 0027
"""
from alembic import op
import sqlalchemy as sa


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("channel_configs_channel_key", "channel_configs", type_="unique")
    op.add_column("channel_configs", sa.Column("label", sa.String(120)))
    op.create_unique_constraint("uq_channel_account", "channel_configs", ["channel", "account_id"])
    op.create_index("ix_channel_configs_channel", "channel_configs", ["channel"])
    op.add_column("channel_identities", sa.Column("account_id", sa.String(64)))


def downgrade() -> None:
    op.drop_column("channel_identities", "account_id")
    op.drop_index("ix_channel_configs_channel", table_name="channel_configs")
    op.drop_constraint("uq_channel_account", "channel_configs", type_="unique")
    op.drop_column("channel_configs", "label")
    # only possible again while each channel has at most one account
    op.create_unique_constraint("channel_configs_channel_key", "channel_configs", ["channel"])
