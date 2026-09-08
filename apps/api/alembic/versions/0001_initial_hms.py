"""Initial HMS PostgreSQL schema

Revision ID: 0001_initial_hms
Revises:
Create Date: 2026-09-08
"""
from alembic import op

revision = "0001_initial_hms"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The SQLAlchemy metadata is the authoritative schema definition. create_all
    # only creates missing tables, making this bootstrap usable for a migrated
    # existing database as well as a fresh PostgreSQL installation.
    from app.db import Base
    import app.models  # noqa: F401
    import app.pms_core  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    raise RuntimeError("The initial HMS schema is not destructively downgraded. Restore a verified database backup instead.")
