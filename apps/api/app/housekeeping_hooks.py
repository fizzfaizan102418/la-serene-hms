from datetime import datetime
from secrets import token_hex

from sqlalchemy import event, inspect, insert, select
from sqlalchemy.orm import Session

from .models import BusinessDateState, Room
from .housekeeping_control import housekeeping_tasks


@event.listens_for(Session, "before_flush")
def create_housekeeping_task_when_room_becomes_dirty(session: Session, flush_context, instances) -> None:
    """Ensure a Front Desk checkout that dirties a room creates its cleaning work atomically."""
    for room in list(session.dirty):
        if not isinstance(room, Room) or room.status != "dirty":
            continue
        history = inspect(room).attrs.status.history
        if "dirty" not in history.added:
            continue
        state = session.get(BusinessDateState, 1)
        if state is None:
            # Legacy/local sessions without the persisted business-date authority
            # cannot safely stamp an operational task. Production PostgreSQL
            # installs have a database trigger that rejects such inserts.
            continue
        existing = session.execute(
            select(housekeeping_tasks.c.id).where(
                housekeeping_tasks.c.room_id == room.id,
                housekeeping_tasks.c.status.in_(("pending", "in_progress")),
            ).limit(1)
        ).first()
        if existing is not None:
            continue
        stamp = state.current_business_date.strftime("%Y%m%d")
        session.execute(insert(housekeeping_tasks).values(
            task_no=f"HK-{stamp}-{token_hex(4).upper()}",
            room_id=room.id,
            business_date=state.current_business_date,
            task_type="checkout_clean",
            status="pending",
            priority="normal",
            reason="Room requires cleaning after checkout",
            created_by=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
