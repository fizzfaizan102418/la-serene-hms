from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .logging_config import configure_production_logging

configure_production_logging()

from .main import app as api_app  # noqa: E402
from .migration_guard import check_database_at_head  # noqa: E402
from .readiness import router as readiness_router  # noqa: E402
from .finance_controls import router as finance_controls_router  # noqa: E402
from .housekeeping import router as housekeeping_router  # noqa: E402
from .inventory import router as inventory_router  # noqa: E402
from .night_audit import router as night_audit_router  # noqa: E402
from .purchasing import router as purchasing_router  # noqa: E402
from .reports import router as reports_router  # noqa: E402
from .restaurant_pos import router as restaurant_pos_router  # noqa: E402


WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

if not WEB_DIST.is_dir():
    raise RuntimeError(
        f"Production web build not found at {WEB_DIST}. "
        "Run scripts/windows/build-web.ps1 before starting the production service."
    )

# Fail closed: the production service must never start against a pending or
# divergent Alembic schema. Database upgrades are performed by the K12 lifecycle
# script before the service is restarted.
check_database_at_head()

app: FastAPI = api_app

# The core reservation/billing routers are mounted by main.py. These operational
# routers are production capabilities too, but are kept out of the legacy SQLite
# development entrypoint until their production PostgreSQL lifecycle is active.
# Prefixes are normalized here to match the frontend contract.
app.include_router(readiness_router)
app.include_router(housekeeping_router)
app.include_router(inventory_router)
app.include_router(purchasing_router)
app.include_router(restaurant_pos_router)
app.include_router(finance_controls_router, prefix="/api")
app.include_router(reports_router, prefix="/api")
app.include_router(night_audit_router, prefix="/api")


# API routes are registered before this catch-all static mount. StaticFiles(html=True)
# provides the React index.html for browser navigation paths that are not API routes.
app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
