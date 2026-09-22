from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from .financial_authority import post_folio_charge_authoritative
from .folio_integrity import item_has_active_charge
from .models import FinancialTransaction, Folio, FolioItem, LedgerEntry, Reservation, Room, StayRateSegment
from .pms_core import Stay

MONEY = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _charged_room_nights(db: Session, *, stay_id: int) -> Decimal:
    """Return active room nights already charged for a stay.

    Room-charge quantity is the authoritative count of nights covered by
    active folio charges. Reversed folio items are excluded so Night Audit
    can safely post only genuinely uncharged nights.
    """
    items = db.scalars(
        select(FolioItem)
        .where(
            FolioItem.stay_id == stay_id,
            FolioItem.category == "room",
        )
        .order_by(FolioItem.id)
    ).all()

    total = Decimal("0")
    for item in items:
        if item_has_active_charge(db, item.id):
            total += Decimal(str(item.quantity))
    return total


def _elapsed_room_nights(*, stay: Stay, business_date: date) -> Decimal:
    """Return room nights elapsed through the end of the business date."""
    cutoff = min(stay.check_out, business_date.fromordinal(business_date.toordinal() + 1))
    return Decimal(max(0, (cutoff - stay.check_in).days))


def _remaining_room_nights(db: Session, *, stay: Stay, business_date: date) -> Decimal:
    """Return elapsed room nights not yet covered by active folio charges."""
    elapsed = _elapsed_room_nights(stay=stay, business_date=business_date)
    charged = _charged_room_nights(db, stay_id=stay.id)
    return max(Decimal("0"), elapsed - charged)


def preview_room_charges_for_business_date(db: Session, *, business_date: date) -> list[dict]:
    """Return room-night charges that Night Audit would post, without mutating data."""
    stays = db.scalars(
        select(Stay).where(
            Stay.status == "checked_in",
            Stay.check_in <= business_date,
            Stay.check_out >= business_date,
        ).order_by(Stay.id)
    ).all()

    preview: list[dict] = []
    for stay in stays:
        folio = db.scalar(
            select(Folio)
            .where(Folio.reservation_id == stay.reservation_id)
            .order_by(Folio.id)
            .limit(1)
        )
        if folio is None or folio.status != "open":
            continue

        room = db.get(Room, stay.room_id)
        if room is None:
            continue

        if _remaining_room_nights(db, stay=stay, business_date=business_date) <= 0:
            continue

        description = f"Night audit · {business_date.isoformat()} · stay #{stay.id} · room {room.number}"
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

        preview.append({
            "stay_id": stay.id,
            "reservation_id": stay.reservation_id,
            "folio_id": folio.id,
            "room_id": room.id,
            "room": room.number,
            "gross_amount": gross_rate,
            "discount_amount": discount,
            "amount": net_rate,
            "description": description,
        })
    retudef accrue_room_charges_for_business_date(db: Session, *, business_date: date, created_by: int) -> int:
    """Reconcile every missing room night for the supplied business date.

    The exact stay + business-date reconciler is the single source of truth for
    Night Audit. This prevents one room's posting from satisfying another room
    and makes repeated runs idempotent.
    """
    from .room_charge_integrity import post_accrued_room_charges

    reservation_ids = db.scalars(
        select(Folio.reservation_id)
        .join(Stay, Stay.reservation_id == Folio.reservation_id)
        .where(
            Folio.status == "open",
            Stay.status == "checked_in",
            Stay.check_in <= business_date,
            Stay.check_out >= business_date,
        )
        .distinct()
    ).all()

    posted = 0
    for reservation_id in reservation_ids:
        reservation = db.get(Reservation, reservation_id)
        folio = db.scalar(
            select(Folio)
            .where(Folio.reservation_id == reservation_id, Folio.status == "open")
            .order_by(Folio.id)
            .limit(1)
        )
        if reservation is None or folio is None:
            continue
        posted += post_accrued_room_charges(
            db, reservation, folio, business_date, created_by
        )
    return posted
