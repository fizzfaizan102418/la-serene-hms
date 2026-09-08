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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
