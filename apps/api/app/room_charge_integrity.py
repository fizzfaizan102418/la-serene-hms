from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .financial_authority import post_folio_charge_authoritative
from .models import FinancialTransaction, Folio, FolioItem, LedgerEntry, Reservation, Room, StayRateSegment
from .pms_core import Stay

MONEY = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _room_charge_posted_for_date(
    db: Session,
    folio_id: int,
    stay_id: int,
    posting_date: date,
) -> bool:
    """Return True only when this exact stay/night has an authoritative posting."""
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


def _rate_for_night(
    db: Session,
    stay: Stay,
    night: date,
) -> tuple[Decimal, Decimal]:
    """Return gross nightly rate and discount for the exact night."""
    segment = db.scalar(
        select(StayRateSegment)
        .where(
            StayRateSegment.stay_id == stay.id,
            StayRateSegment.from_date <= night,
            StayRateSegment.to_date > night,
        )
        .order_by(StayRateSegment.from_date.desc(), StayRateSegment.id.desc())
        .limit(1)
    )
    if segment is None:
        return money(stay.agreed_rate + stay.discount_amount), money(stay.discount_amount)
    return money(segment.rate), money(segment.discount_amount)


def post_accrued_room_charges(
    db: Session,
    reservation: Reservation,
    folio: Folio,
    business_date: date,
    user_id: int,
) -> int:
    """Post every missing elapsed room night independently for every stay.

    The authoritative key is (folio, stay, business_date). This is deliberately
    stronger than counting folio quantities: an existing charge on Room B cannot
    satisfy a missing night on Room A, and an aggregate multi-night item cannot
    hide a missing business-date posting.
    """
    locked_folio = db.scalar(
        select(Folio).where(Folio.id == folio.id).with_for_update()
    )
    if locked_folio is None:
        raise HTTPException(status_code=404, detail="Folio not found")
    folio = locked_folio

    posted = 0
    stays = db.scalars(
        select(Stay)
        .where(Stay.reservation_id == reservation.id)
        .order_by(Stay.id)
    ).all()

    for stay in stays:
        room = db.get(Room, stay.room_id)
        if room is None:
            raise HTTPException(
                status_code=409,
                detail=f"Stay {stay.id} references an invalid room",
            )

        cutoff = min(stay.check_out, business_date + timedelta(days=1))
        night = stay.check_in

        while night < cutoff:
            if not _room_charge_posted_for_date(
                db,
                folio.id,
                stay.id,
                night,
            ):
                gross_rate, discount = _rate_for_night(db, stay, night)
                net_amount = money(gross_rate - discount)

                if net_amount > 0:
                    item = FolioItem(
                        folio_id=folio.id,
                        stay_id=stay.id,
                        description=(
                            f"Room {room.number} · stay #{stay.id} · "
                            f"night {night}"
                        ),
                        category="room",
                        quantity=Decimal("1"),
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
                        amount=net_amount,
                        stay_id=stay.id,
                        category="room",
                        created_by=user_id,
                        gross_amount=gross_rate,
                        discount_amount=discount,
                    )
                    posted += 1

            night += timedelta(days=1)

    return posted
