from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import engine
from .schemas import HealthResponse

router = APIRouter()


def check_database_ready() -> None:
    """Raise if the configured database cannot execute a basic readiness query."""
    with Session(engine) as db:
        db.execute(text("SELECT 1"))


@router.get("/api/ready", response_model=HealthResponse, tags=["health"])
def readiness() -> HealthResponse:
    try:
        check_database_ready()
    except Exception as exc:
        # Keep the externally visible failure deliberately generic. Database
        # exceptions can contain connection details or other sensitive data.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database readiness check failed",
        ) from exc

    return HealthResponse(
        status="ready",
        service="la-serene-hms-api",
        mode="postgresql",
    )
