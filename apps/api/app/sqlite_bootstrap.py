from datetime import date, datetime

from sqlalchemy import select

from .db import Base, IS_SQLITE, SessionLocal, engine, ensure_schema_compatibility


def register_metadata() -> None:
    """Import every module that contributes HMS tables to Base.metadata."""
    if not IS_SQLITE:
        return

    from . import financial_models  # noqa: F401
    from . import housekeeping_control  # noqa: F401
    from . import inventory  # noqa: F401
    from . import models  # noqa: F401
    from . import phase_a_completion  # noqa: F401
    from . import pms_core  # noqa: F401
    from . import purchasing  # noqa: F401
    from . import stay_lifecycle  # noqa: F401


def initialize_sqlite_database() -> None:
    """Create a complete fresh SQLite development database when needed.

    PostgreSQL production initialization remains owned by Alembic. This path
    exists only for the local SQLite development fallback.
    """
    if not IS_SQLITE:
        return

    register_metadata()
    Base.metadata.create_all(bind=engine)

    # Apply compatibility fixes for databases created by older local builds.
    ensure_schema_compatibility()

    from .models import BusinessDateState

    with SessionLocal() as db:
        state = db.scalar(select(BusinessDateState).where(BusinessDateState.id == 1))
        if state is None:
            now = datetime.utcnow()
            db.add(
                BusinessDateState(
                    id=1,
                    current_business_date=date.today(),
                    opened_at=now,
                    updated_at=now,
                )
            )
            db.commit()
