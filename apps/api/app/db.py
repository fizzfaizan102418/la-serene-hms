from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR / 'la_serene_hms.sqlite3'}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def ensure_schema_compatibility() -> None:
    """Apply small, idempotent SQLite compatibility changes for existing installs."""
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

        # Financial Ledger 2.0: keep older installations compatible with the new
        # stay-linked room charge source. SQLite accepts the nullable integer column
        # before the newer ORM metadata references stays as a foreign key.
        if "folio_items" in tables:
            folio_columns = {column["name"] for column in inspect(connection).get_columns("folio_items")}
            if "stay_id" not in folio_columns:
                connection.execute(text("ALTER TABLE folio_items ADD COLUMN stay_id INTEGER"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS idx_folio_items_stay ON folio_items(stay_id)"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# The app historically used Base.metadata.create_all(), so existing installations
# may already have the reservations table before newer columns are introduced.
# Apply the compatibility upgrade as soon as the database module loads.
ensure_schema_compatibility()
