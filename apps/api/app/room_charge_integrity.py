from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import Integer, and_, cast, or_, select
from sqlalchemy.orm import Session

from .financial_authority import post_folio_charge_authoritative
from .models import FinancialTransaction, Folio, FolioItem, LedgerEntry, Reservation, Room, StayRateSegment
from .pms_core import Stay

MONEY = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _elapsed_room_nights(*, stay: Stay, business_date: date) -> Decimal:
    """Return contracted room nights elapsed through the end of the business date."""
    cutoff = min(
        stay.check_out,
        date.fromordinal(business_date.toordinal() + 1),
    )
    return Decimal(max(0, (cutoff - stay.check_in).days))


def _charged_room_nights(db: Session, *, stay_id: int) -> Decimal:
    """Count active room-charge quantities already posted for this stay."""
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
        transaction = db.scalar(
            select(FinancialTransaction.id)
            .join(
                LedgerEntry,
                LedgerEntry.transaction_id == FinancialTransaction.id,
            )
            .where(
                FinancialTransaction.folio_id == item.folio_id,
                FinancialTransaction.reference_type == "folio_item",
                cast(FinancialTransaction.reference_id, Integer) == item.id,
                FinancialTransaction.transaction_type == "folio_charge",
                FinancialTransaction.status == "posted",
                LedgerEntry.account == "Revenue - room",
                LedgerEntry.direction == "credit",
            )
            .limit(1)
        )
        if transaction is not None:
            total += Decimal(str(item.quantity))
    return total


def _room_charge_posted_for_date(
    db: Session,
    folio_id: int,
    stay_id: int,
    posting_date: date,
) -> bool:
    """Return True only when this exact stay/night has an authoritative posting.

    Legacy authoritative folio charges may have omitted LedgerEntry.stay_id.
    In that case, resolve the transaction's folio-item reference back to the
    item's stay so the original posting still counts as the exact stay/night.
    """
    return db.scalar(
        select(FinancialTransaction.id)
        .join(LedgerEntry, LedgerEntry.transaction_id == FinancialTransaction.id)
        .outerjoin(
            FolioItem,
            and_(
                FinancialTransaction.reference_type == "folio_item",
                cast(FinancialTransaction.reference_id, Integer) == FolioItem.id,
            ),
        )
        .where(
            FinancialTransaction.folio_id == folio_id,
            FinancialTransaction.business_date == posting_date,
            FinancialTransaction.transaction_type == "folio_charge",
            FinancialTransaction.status == "posted",
            LedgerEntry.folio_id == folio_id,
            LedgerEntry.account == "Revenue - room",
            LedgerEntry.direction == "credit",
            or_(
                LedgerEntry.stay_id == stay_id,
                FolioItem.stay_id == stay_id,
            ),
        )
        .limit(1)
    ) is not None


def _rate_for_night(db: Session, stay: Stay, posting_date: date) -> tuple[Decimal, Decimal]:
    segment = db.scalar(
        select(StayRateSegment)
        .where(
            StayRateSegment.stay_id == stay.id,
            StayRateSegment.from_date <= posting_date,
            StayRateSegment.to_date > posting_date,
        )
        .order_by(StayRateSegment.from_date, StayRateSegment.id)
        .limit(1)
    )
    if segment is not None:
        return money(segment.rate), money(segment.discount_amount)
    return money(stay.agreed_rate), money(stay.discount_amount)


def post_accrued_room_charges(
    db: Session,
    reservation: Reservation,
    folio: Folio,
    business_date: date,
    user_id: int,
) -> int:
    """Post missing room nights for every stay in a reservation.

    Reconciliation is exact to stay + business date, so a posting for one room
    can never satisfy a missing night for another room in the same folio.
    The operation is idempotent for a given stay/date.
    """
    if folio.status != "open":
        return 0

    stays = db.scalars(
        select(Stay)
        .where(
            Stay.reservation_id == reservation.id,
            Stay.status == "checked_in",
            Stay.check_in <= business_date,
            Stay.check_out >= business_date,
        )
        .order_by(Stay.id)
    ).all()

    # Serialize nightly posting for this folio so two concurrent Night Audit /
    # Front Desk requests cannot both observe the same night as missing.
    db.scalar(select(Folio.id).where(Folio.id == folio.id).with_for_update())

    posted = 0
    for stay in stays:
        # Never post more active room nights than the stay has elapsed through
        # this business date. Exact stay/date idempotency alone is insufficient:
        # it can still create a third charge on a checkout date after the stay's
        # contracted nights are already fully covered.
        elapsed_nights = _elapsed_room_nights(stay=stay, business_date=business_date)
        charged_nights = _charged_room_nights(db, stay_id=stay.id)
        if charged_nights >= elapsed_nights:
            continue

        if _room_charge_posted_for_date(db, folio.id, stay.id, business_date):
            continue

        room = db.get(Room, stay.room_id)
        if room is None:
            continue

        room_charge_key = f"room-night:{folio.id}:{stay.id}:{business_date.isoformat()}"
        existing = db.scalar(
            select(FinancialTransaction.id).where(
                FinancialTransaction.idempotency_key == room_charge_key,
                FinancialTransaction.status == "posted",
            )
        )
        if existing is not None:
            continue

        gross_rate, discount = _rate_for_night(db, stay, business_date)
        net_rate = money(max(Decimal("0.00"), gross_rate - discount))
        if net_rate <= 0:
            continue

        description = (
            f"Night audit · {business_date.isoformat()} · "
            f"stay #{stay.id} · room {room.number}"
        )
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
            reservation_id=reservation.id,
            item_id=item.id,
            amount=net_rate,
            stay_id=stay.id,
            category="room",
            created_by=user_id,
            gross_amount=gross_rate,
            discount_amount=discount,
            idempotency_key=room_charge_key,
        )
        posted += 1

    return posted
