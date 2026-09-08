"""Add per-room folio item routing for Phase A.

Revision ID: 0006_phase_a_folio_item_routing
Revises: 0005_stay_folio_windows
Create Date: 2026-09-08
"""
from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa

revision = "0006_phase_a_folio_item_routing"
down_revision = "0005_stay_folio_windows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "folio_item_routing" not in inspector.get_table_names():
        op.create_table(
            "folio_item_routing",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("folio_item_id", sa.Integer(), sa.ForeignKey("folio_items.id", ondelete="CASCADE"), nullable=False),
            sa.Column("window_id", sa.Integer(), sa.ForeignKey("stay_folio_windows.id", ondelete="CASCADE"), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("folio_item_id", name="uq_folio_item_routing_item"),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("folio_item_routing")}
    if "ix_folio_item_routing_folio_item_id" not in indexes:
        op.create_index("ix_folio_item_routing_folio_item_id", "folio_item_routing", ["folio_item_id"])
    if "ix_folio_item_routing_window_id" not in indexes:
        op.create_index("ix_folio_item_routing_window_id", "folio_item_routing", ["window_id"])


def downgrade() -> None:
    op.drop_index("ix_folio_item_routing_window_id", table_name="folio_item_routing")
    op.drop_index("ix_folio_item_routing_folio_item_id", table_name="folio_item_routing")
    op.drop_table("folio_item_routing")
