from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from .financial_authority import post_folio_charge_authoritative
from .folio_integrity import item_has_active_charge
from .models import FinancialTransaction, Folio, FolioItem, LedgerEntry, Room, StayRateSegment
from .pms_core import Stay

MONEY = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _has_active_room_charge_for_date(db: Session, *, stay_id: int, business_date: date) -> bool:
    """Return whether an active folio room charge already covers this stay/date.

    Night Audit idempotency is based on the authoritative financial transaction,
    not the human-readable folio-item description. This keeps manual/operational
    room charges from being duplicated by Night Audit while remaining date-scoped
    for multi-night stays.
    """
    transactions = db.scalars(
        select(FinancialTransaction)
        .join(LedgerEntry, LedgerEntry.transaction_id == FinancialTransaction.id)
        .where(
            FinancialTransaction.transaction_type == "folio_charge",
            FinancialTransaction.status == "posted",
            FinancialTransaction.business_date == business_date,
            FinancialTransaction.reference_type == "folio_item",
            FinancialTransaction.reference_id.is_not(None),
            LedgerEntry.stay_id == stay_id,
            LedgerEntry.account == "Guest Receivables",
        )
    ).all()
    return any(item_has_active_charge(db, int(tx.reference_id)) for tx in transactions if tx.reference_id and tx.reference_id.isdigit())


def preview_room_charges_for_business_date(db: Session, *, business_date: date) -> list[dict]:
    """Return room-night charges that Night Audit would post, without mutating data."""
    stays = db.scalars(
        select(Stay).where(
            Stay.status == "checked_in",
            Stay.check_in <= business_date,
            Stay.check_out > business_date,
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

        if _has_active_room_charge_for_date(db, stay_id=stay.id, business_date=business_date):
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
    return preview


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

        if _has_active_room_charge_for_date(db, stay_id=stay.id, business_date=business_date):
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
