from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .logging_config import configure_production_logging

configure_production_logging()

from .main import app as api_app  # noqa: E402
from .migration_guard import check_database_at_head  # noqa: E402
from .readiness import router as readiness_router  # noqa: E402

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

if not WEB_DIST.is_dir():
    raise RuntimeError(
        f"Production web build not found at {WEB_DIST}. "
        "Run scripts/windows/build-web.ps1 before starting the production service."
    )

check_database_at_head()

app: FastAPI = api_app

app.include_router(readiness_router)

app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
