from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Production uses PostgreSQL through HMS_DATABASE_URL. SQLite remains the explicit
# local-development fallback so existing developer databases can still be opened
# during the migration period.
DATABASE_URL = settings.database_url or f"sqlite:///{DATA_DIR / 'la_serene_hms.sqlite3'}"
IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine_kwargs = {"pool_pre_ping": True}
if IS_SQLITE:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def ensure_schema_compatibility() -> None:
    """Keep legacy SQLite development installs compatible during the PG migration."""
    if not IS_SQLITE:
        return
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "reservations" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("reservations")}
    additions = []
    if "checked_in_at" not in columns:
        additions.append("ALTER TABLE reservations ADD COLUMN checked_in_at DATETIME")
    if "checked_out_at" not in columns:
        additions.append("ALTER TABLE reservations ADD COLUMN checked_out_at DATETIME")

    if additions:
        with engine.begin() as connection:
            for statement in additions:
                connection.execute(text(statement))

    with engine.begin() as connection:
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_reservations_checked_in_at ON reservations(checked_in_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_reservations_checked_out_at ON reservations(checked_out_at)"))

        if "folio_items" in tables:
            folio_columns = {column["name"] for column in inspect(connection).get_columns("folio_items")}
            if "stay_id" not in folio_columns:
                connection.execute(text("ALTER TABLE folio_items ADD COLUMN stay_id INTEGER"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS idx_folio_items_stay ON folio_items(stay_id)"))

            if "stays" in tables and "rooms" in tables:
                connection.execute(text(
                    "UPDATE folio_items "
                    "SET stay_id = (SELECT s.id FROM stays s "
                    "JOIN folios f ON f.id = folio_items.folio_id "
                    "JOIN rooms r ON r.id = s.room_id "
                    "WHERE s.reservation_id = f.reservation_id "
                    "AND folio_items.category = 'room' "
                    "AND folio_items.description LIKE 'Room ' || r.number || ' ·%' "
                    "LIMIT 1) "
                    "WHERE folio_items.category = 'room' AND folio_items.stay_id IS NULL"
                ))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


ensure_schema_compatibility()
