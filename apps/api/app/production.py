from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .main import app as api_app


WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

if not WEB_DIST.is_dir():
    raise RuntimeError(
        f"Production web build not found at {WEB_DIST}. "
        "Run scripts/windows/build-web.ps1 before starting the production service."
    )

# API routes are registered before this catch-all static mount. StaticFiles(html=True)
# provides the React index.html for browser navigation paths that are not API routes.
app: FastAPI = api_app
app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
