from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from .financial_authority import post_folio_charge_authoritative
from .models import Folio, FolioItem, ReservationRoom, Room, StayRateSegment
from .pms_core import Stay

MONEY = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def accrue_room_charges_for_business_date(db: Session, *, business_date: date, created_by: int) -> int:
    """Post exactly one authoritative room-night per active stay for a business date.

    The operation is deliberately date-scoped and idempotent. Re-running it for the
    same stay/date does not create another folio item. Reversed historical charges do
    not suppress a new charge for a later business date.
    """
    stays = db.scalars(
        select(Stay).where(
            Stay.status == "checked_in",
            Stay.check_in <= business_date,
            Stay.check_out > business_date,
        ).order_by(Stay.id)
    ).all()

    posted = 0
    for stay in stays:
        folio = db.scalar(select(Folio).where(Folio.reservation_id == stay.reservation_id).order_by(Folio.id).limit(1))
        if folio is None or folio.status != "open":
            continue

        room = db.get(Room, stay.room_id)
        if room is None:
            continue

        description = f"Night audit · {business_date.isoformat()} · stay #{stay.id} · room {room.number}"
        existing = db.scalar(
            select(FolioItem.id).where(
                FolioItem.folio_id == folio.id,
                FolioItem.stay_id == stay.id,
                FolioItem.category == "room",
                FolioItem.description == description,
            ).limit(1)
        )
        if existing is not None:
            continue

        segment = db.scalar(
            select(StayRateSegment)
            .where(
                StayRateSegment.stay_id == stay.id,
                StayRateSegment.from_date <= business_date,
                StayRateSegment.to_date > business_date,
            )
            .order_by(StayRateSegment.from_date, StayRateSegment.id)
            .limit(1)
        )

        if segment is not None:
            gross_rate = money(segment.rate)
            discount = money(segment.discount_amount)
        else:
            gross_rate = money(stay.agreed_rate)
            discount = money(stay.discount_amount)

        net_rate = money(max(Decimal("0.00"), gross_rate - discount))
        if net_rate <= 0:
            continue

        item = FolioItem(
            folio_id=folio.id,
            stay_id=stay.id,
            description=description,
            category="room",
            quantity=1,
            unit_price=gross_rate,
            discount=discount,
        )
        db.add(item)
        db.flush()
        post_folio_charge_authoritative(
            db,
            folio_id=folio.id,
            reservation_id=stay.reservation_id,
            item_id=item.id,
            amount=net_rate,
            stay_id=stay.id,
            category="room",
            created_by=created_by,
            gross_amount=gross_rate,
            discount_amount=discount,
        )
        posted += 1

    return posted
