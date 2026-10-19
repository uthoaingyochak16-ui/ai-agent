"""family members: relatives the guest talks about, kept apart from the guest's own details

Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('family_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('relation', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=True),
    sa.Column('age', sa.Integer(), nullable=True),
    sa.Column('blood_group', sa.String(length=4), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
    sa.Column('source', sa.String(length=16), nullable=False, server_default='ai'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_family_members_profile_id', 'family_members', ['profile_id'])


def downgrade() -> None:
    op.drop_index('ix_family_members_profile_id', table_name='family_members')
    op.drop_table('family_members')
