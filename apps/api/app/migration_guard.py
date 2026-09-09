from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from .config import settings


API_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"


def schema_revision_status(current_revisions: list[str], heads: list[str]) -> str:
    """Return a deterministic lifecycle status for a single-head Alembic schema."""
    current = [revision for revision in current_revisions if revision]
    expected = [revision for revision in heads if revision]
    if len(expected) != 1:
        raise RuntimeError(f"Expected exactly one Alembic head, found {len(expected)}")
    if len(current) != 1:
        raise RuntimeError(f"Expected exactly one current Alembic revision, found {len(current)}")
    if current[0] != expected[0]:
        raise RuntimeError(
            f"Database schema revision {current[0]} does not match application head {expected[0]}. "
            "Run the controlled production database upgrade before starting the service."
        )
    return current[0]


def check_database_at_head(database_url: str | None = None) -> str:
    """Fail closed when the production database is not exactly at the app migration head."""
    url = (database_url or settings.database_url).strip()
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("Production migration guard requires a PostgreSQL database URL")

    alembic_config = Config(str(ALEMBIC_INI))
    alembic_config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    heads = list(ScriptDirectory.from_config(alembic_config).get_heads())

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars().all()
    except Exception as exc:
        raise RuntimeError(
            "Production database migration state could not be read. "
            "Run the controlled database upgrade and verify connectivity."
        ) from exc
    finally:
        engine.dispose()

    return schema_revision_status(list(rows), heads)
