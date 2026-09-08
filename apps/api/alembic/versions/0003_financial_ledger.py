"""Create immutable financial ledger tables.

Revision ID: 0003_financial_ledger
Revises: 0002_backfill_phase_a
Create Date: 2026-09-08
"""
from alembic import op

revision = "0003_financial_ledger"
down_revision = "0002_backfill_phase_a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Financial ledger tables are already represented by SQLAlchemy metadata.
    # create_all is used here to preserve the project's bootstrap migration
    # strategy while keeping the revision idempotent for fresh databases.
    from app.db import Base
    import app.models  # noqa: F401
    import app.pms_core  # noqa: F401
    from app import ledger  # noqa: F401
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    raise RuntimeError("Financial ledger history is append-only; restore a verified backup instead of dropping journal tables.")
