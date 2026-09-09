from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import engine
from .schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/api/ready", response_model=HealthResponse)
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
