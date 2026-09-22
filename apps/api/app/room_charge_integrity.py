from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .financial_authority import post_folio_charge_authoritative
from .folio_integrity import item_has_active_charge
from .models import Folio, FolioItem, Reservation, Room, StayRateSegment
from .pms_core import Stay

MONEY = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _charged_room_nights(db: Session, stay_id: int) -> Decimal:
    """Return active room nights already covered by this stay's folio charges."""
    items = db.scalars(
        select(FolioItem)
        .where(FolioItem.stay_id == stay_id, FolioItem.category == "room")
        .order_by(FolioItem.id)
    ).all()
    total = Decimal("0")
    for item in items:
        if item_has_active_charge(db, item.id):
            total += Decimal(str(item.quantity))
    return total


def _elapsed_room_nights(stay: Stay, business_date: date) -> Decimal:
    """Return nights elapsed through the end of the supplied business date."""
    cutoff = min(stay.check_out, business_date + timedelta(days=1))
    return Decimal(max(0, (cutoff - stay.check_in).days))


def _rate_segments(db: Session, stay: Stay) -> list[StayRateSegment]:
    segments = db.scalars(
        select(StayRateSegment)
        .where(StayRateSegment.stay_id == stay.id)
        .order_by(StayRateSegment.from_date, StayRateSegment.id)
    ).all()
    if segments:
        return segments
    return [
        StayRateSegment(
            from_date=stay.check_in,
            to_date=stay.check_out,
            rate=money(stay.agreed_rate + stay.discount_amount),
            discount_percent=stay.discount_percent,
            discount_amount=stay.discount_amount,
            source="reservation",
        )
    ]


def _room_charge_posted_for_date(db: Session, folio_id: int, stay_id: int, posting_date: date) -> bool:
    """Compatibility helper: confirm a posted room transaction for stay/date."""
    from .models import FinancialTransaction, LedgerEntry

    return db.scalar(
        select(FinancialTransaction.id)
        .join(LedgerEntry, LedgerEntry.transaction_id == FinancialTransaction.id)
        .where(
            FinancialTransaction.folio_id == folio_id,
            FinancialTransaction.business_date == posting_date,
            FinancialTransaction.transaction_type == "folio_charge",
            FinancialTransaction.status == "posted",
            LedgerEntry.folio_id == folio_id,
            LedgerEntry.stay_id == stay_id,
            LedgerEntry.account == "Revenue - room",
            LedgerEntry.direction == "credit",
        )
        .limit(1)
    ) is not None


def post_accrued_room_charges(
    db: Session,
    reservation: Reservation,
    folio: Folio,
    business_date: date,
    user_id: int,
) -> int:
    """Reconcile elapsed room nights independently for every stay.

    The reconciliation is intentionally stay-scoped rather than reservation-scoped.
    A two-room reservation therefore cannot let a charge on Room B satisfy a missing
    night on Room A. Existing active room-charge quantities are preserved, so retries
    and stay extensions only post the remaining elapsed nights. Charges are aggregated
    by contiguous rate segment to preserve the existing folio presentation and ledger
    behavior.
    """
    locked_folio = db.scalar(select(Folio).where(Folio.id == folio.id).with_for_update())
    if locked_folio is None:
        raise HTTPException(status_code=404, detail="Folio not found")
    folio = locked_folio

    posted = 0
    stays = db.scalars(
        select(Stay).where(Stay.reservation_id == reservation.id).order_by(Stay.id)
    ).all()

    for stay in stays:
        elapsed_nights = _elapsed_room_nights(stay, business_date)
        charged_nights = _charged_room_nights(db, stay.id)
        remaining_nights = elapsed_nights - charged_nights
        if remaining_nights <= 0:
            continue

        room = db.get(Room, stay.room_id)
        if room is None:
            raise HTTPException(status_code=409, detail=f"Stay {stay.id} references an invalid room")

        remaining_skip = charged_nights
        remaining_to_post = remaining_nights
        for segment in _rate_segments(db, stay):
            segment_start = max(segment.from_date, stay.check_in)
            segment_end = min(segment.to_date, business_date + timedelta(days=1), stay.check_out)
            segment_nights = Decimal(max(0, (segment_end - segment_start).days))
            if segment_nights <= 0:
                continue

            skip = min(remaining_skip, segment_nights)
            remaining_skip -= skip
            billable = min(segment_nights - skip, remaining_to_post)
            if billable <= 0:
                continue

            gross_rate = money(segment.rate)
            discount_per_night = money(segment.discount_amount)
            gross_amount = money(gross_rate * billable)
            discount_total = money(discount_per_night * billable)
            net_amount = money(gross_amount - discount_total)
            if net_amount <= 0:
                continue

            item = FolioItem(
                folio_id=folio.id,
                stay_id=stay.id,
                description=f"Room {room.number} · stay #{stay.id} · {int(billable)} night(s) · through {business_date}",
                category="room",
                quantity=billable,
                unit_price=gross_rate,
                discount=discount_total,
            )
            db.add(item)
            db.flush()
            post_folio_charge_authoritative(
                db,
                folio_id=folio.id,
                reservation_id=reservation.id,
                item_id=item.id,
                amount=net_amount,
                stay_id=stay.id,
                category="room",
                created_by=user_id,
                gross_amount=gross_amount,
                discount_amount=discount_total,
            )
            posted += 1
            remaining_to_post -= billable
            if remaining_to_post <= 0:
                break

    return posted
