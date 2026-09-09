from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import BusinessDateState


def get_current_business_date(db: Session, *, fallback_to_today: bool = False) -> date:
    state = db.scalar(select(BusinessDateState).order_by(BusinessDateState.id).limit(1))
    if state and state.current_business_date:
        return state.current_business_date
    if fallback_to_today:
        return date.today()
    raise HTTPException(status_code=503, detail="Business date is not initialized")


def lock_current_business_date(db: Session) -> BusinessDateState:
    """Return the singleton business-date row with a PostgreSQL row lock."""
    state = db.scalar(
        select(BusinessDateState)
        .where(BusinessDateState.id == 1)
        .with_for_update()
    )
    if state is None or state.current_business_date is None:
        raise HTTPException(status_code=503, detail="Business date is not initialized")
    return state
