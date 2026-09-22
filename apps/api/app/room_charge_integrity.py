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
