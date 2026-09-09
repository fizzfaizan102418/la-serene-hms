from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from .logging_config import configure_production_logging

configure_production_logging()

from .db import engine  # noqa: E402
from .main import app as api_app  # noqa: E402
from .schemas import HealthResponse  # noqa: E402


WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

if not WEB_DIST.is_dir():
    raise RuntimeError(
        f"Production web build not found at {WEB_DIST}. "
        "Run scripts/windows/build-web.ps1 before starting the production service."
    )

app: FastAPI = api_app


@app.get("/api/ready", response_model=HealthResponse, tags=["health"])
def readiness() -> HealthResponse:
    try:
        with Session(engine) as db:
            db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database readiness check failed",
        ) from exc
    return HealthResponse(status="ready", service="la-serene-hms-api", mode="postgresql")


# API routes are registered before this catch-all static mount. StaticFiles(html=True)
# provides the React index.html for browser navigation paths that are not API routes.
app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
