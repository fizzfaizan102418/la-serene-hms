"""Add Phase B financial operations support tables.

Revision ID: 0004_phase_b_financial_ops
Revises: 0003_financial_ledger
Create Date: 2026-09-08
"""
from alembic import op

revision = "0004_phase_b_financial_ops"
down_revision = "0003_financial_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.db import Base
    import app.models  # noqa: F401
    import app.pms_core  # noqa: F401
    import app.ledger  # noqa: F401
    import app.financial_models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    raise RuntimeError("Phase B financial history is append-only; restore a verified backup instead of dropping financial operations tables.")
