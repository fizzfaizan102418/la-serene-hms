"""Add room-level folio routing windows.

Revision ID: 0005_stay_folio_windows
Revises: 0004_phase_b_financial_ops
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_stay_folio_windows"
down_revision = "0004_phase_b_financial_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stay_folio_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("folio_id", sa.Integer(), sa.ForeignKey("folios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stay_id", sa.Integer(), sa.ForeignKey("stays.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("payer_type", sa.String(length=30), nullable=False, server_default="guest"),
        sa.Column("guest_id", sa.Integer(), sa.ForeignKey("guests.id"), nullable=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("booking_groups.id"), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_stay_folio_windows_folio_id", "stay_folio_windows", ["folio_id"])
    op.create_index("ix_stay_folio_windows_stay_id", "stay_folio_windows", ["stay_id"])


def downgrade() -> None:
    op.drop_index("ix_stay_folio_windows_stay_id", table_name="stay_folio_windows")
    op.drop_index("ix_stay_folio_windows_folio_id", table_name="stay_folio_windows")
    op.drop_table("stay_folio_windows")
