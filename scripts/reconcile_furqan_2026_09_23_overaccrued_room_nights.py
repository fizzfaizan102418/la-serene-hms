from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.db import DATA_DIR, SessionLocal
from app.financial_authority import folio_ledger_summary
from app.ledger import reverse_transaction
from app.models import BusinessDateState, FinancialTransaction, Folio, Guest, LedgerEntry, Payment, Reservation
from app.pms_core import Stay

TARGET_RESERVATION_ID = 38
TARGET_FOLIO_ID = 38
TARGET_STAY_IDS = (40, 41)
TARGET_BUSINESS_DATE = date(2026, 9, 23)
EXPECTED_CURRENT_BUSINESS_DATE = date(2026, 9, 23)
EXPECTED_LAST_CLOSED_DATE = date(2026, 9, 22)
EXPECTED_ROOM_CHARGE_AMOUNT = Decimal("10000.00")
EXPECTED_PRE_CORRECTION_TOTAL = Decimal("64000.00")
EXPECTED_PAYMENT_TOTAL = Decimal("44000.00")
EXPECTED_POST_CORRECTION_TOTAL = Decimal("44000.00")
CORRECTION_REASON = (
    "Reverse erroneous 2026-09-23 room-night accruals for Furqan stays "
    "#40 and #41; contracted two-night stay already fully charged."
)


def _matching_transactions(db):
    rows = []
    transactions = db.scalars(
        select(FinancialTransaction)
        .where(
            FinancialTransaction.folio_id == TARGET_FOLIO_ID,
            FinancialTransaction.business_date == TARGET_BUSINESS_DATE,
            FinancialTransaction.transaction_type == "folio_charge",
            FinancialTransaction.status == "posted",
        )
        .order_by(FinancialTransaction.id)
    ).all()

    for tx in transactions:
        entries = db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.transaction_id == tx.id)
            .order_by(LedgerEntry.id)
        ).all()
        room_credit = next(
            (
                entry
                for entry in entries
                if entry.account == "Revenue - room" and entry.direction == "credit"
            ),
            None,
        )
        if room_credit is None or room_credit.stay_id not in TARGET_STAY_IDS:
            continue

        amount = next(
            (
                entry.amount
                for entry in entries
                if entry.account == "Guest Receivables" and entry.direction == "debit"
            ),
            Decimal("0.00"),
        )
        rows.append(
            {
                "transaction": tx,
                "amount": Decimal(amount),
                "stay_id": room_credit.stay_id,
            }
        )
    return rows


def _validate_scope(db):
    state = db.get(BusinessDateState, 1)
    if state is None:
        raise RuntimeError("Business date state not found.")
    if state.current_business_date != EXPECTED_CURRENT_BUSINESS_DATE:
        raise RuntimeError(
            f"Safety stop: current business date is {state.current_business_date}, "
            f"expected {EXPECTED_CURRENT_BUSINESS_DATE}."
        )
    if state.last_closed_business_date != EXPECTED_LAST_CLOSED_DATE:
        raise RuntimeError(
            f"Safety stop: last closed business date is {state.last_closed_business_date}, "
            f"expected {EXPECTED_LAST_CLOSED_DATE}."
        )

    reservation = db.get(Reservation, TARGET_RESERVATION_ID)
    folio = db.get(Folio, TARGET_FOLIO_ID)
    stays = [db.get(Stay, stay_id) for stay_id in TARGET_STAY_IDS]
    if reservation is None or folio is None or any(stay is None for stay in stays):
        raise RuntimeError("Safety stop: target reservation, folio, or stay was not found.")
    if folio.reservation_id != reservation.id or any(
        stay.reservation_id != reservation.id for stay in stays if stay is not None
    ):
        raise RuntimeError("Safety stop: target folio/stays do not belong to reservation #38.")

    guest = db.get(Guest, reservation.guest_id)
    print(f"Guest: {guest.full_name if guest else 'unknown'}")
    print(f"Reservation: #{reservation.id}")
    print(f"Folio: #{folio.id}")
    for stay in stays:
        print(f"Stay: #{stay.id} room_id={stay.room_id} {stay.check_in} -> {stay.check_out}")
    return state, reservation, folio, stays


def _recorded_payments(db):
    payments = db.scalars(
        select(Payment).where(Payment.folio_id == TARGET_FOLIO_ID).order_by(Payment.id)
    ).all()
    total = sum((Decimal(p.amount) for p in payments), Decimal("0.00"))
    print("\nRecorded payments:")
    for payment in payments:
        print(
            f"  Payment #{payment.id}: {Decimal(payment.amount):.2f} "
            f"{payment.method} {payment.reference or 'No reference'}"
        )
    print(f"Recorded payment total: {total:.2f}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Controlled correction for the two erroneous 2026-09-23 room-night "
            "accruals on M. Furqan folio #38."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the validated reversals. Without this flag the script is read-only.",
    )
    args = parser.parse_args()

    DATA_DIR.joinpath("reconciliation").mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        _validate_scope(db)
        payment_total = _recorded_payments(db)
        if payment_total != EXPECTED_PAYMENT_TOTAL:
            raise RuntimeError(
                f"Safety stop: expected recorded payments of PKR {EXPECTED_PAYMENT_TOTAL:.2f}; "
                f"found PKR {payment_total:.2f}."
            )

        summary = folio_ledger_summary(db, TARGET_FOLIO_ID)
        print(
            f"\nCurrent folio totals: total={summary.total:.2f}, "
            f"paid={summary.paid:.2f}, balance={summary.balance:.2f}"
        )
        if summary.total != EXPECTED_PRE_CORRECTION_TOTAL or summary.paid != EXPECTED_PAYMENT_TOTAL:
            raise RuntimeError(
                "Safety stop: folio totals do not match the confirmed pre-correction state."
            )

        rows = _matching_transactions(db)
        print("\nMatching posted 2026-09-23 room-night transactions:")
        for row in rows:
            tx = row["transaction"]
            print(
                f"  TX #{tx.id} | stay #{row['stay_id']} | "
                f"amount={row['amount']:.2f} | idempotency={tx.idempotency_key or 'none'}"
            )

        if len(rows) != 2 or {row["stay_id"] for row in rows} != set(TARGET_STAY_IDS):
            raise RuntimeError(
                "Safety stop: expected exactly one PKR 10,000 posted room charge "
                "for each of stays #40 and #41 on 2026-09-23."
            )
        if any(row["amount"] != EXPECTED_ROOM_CHARGE_AMOUNT for row in rows):
            raise RuntimeError("Safety stop: target room charges are not both exactly PKR 10,000.00.")

        existing_reversals = []
        for row in rows:
            tx_id = row["transaction"].id
            reversal = db.scalar(
                select(FinancialTransaction.id)
                .where(FinancialTransaction.reversal_of_id == tx_id)
                .limit(1)
            )
            if reversal is not None:
                existing_reversals.append((tx_id, reversal))

        if existing_reversals:
            if len(existing_reversals) == 2:
                print("\nBoth target transactions are already reversed; no change made.")
                return 0
            raise RuntimeError(
                f"Safety stop: only some target transactions are already reversed: {existing_reversals}"
            )

        snapshot = {
            "correction": "furqan_overaccrued_checkout_date_room_nights",
            "reservation_id": TARGET_RESERVATION_ID,
            "folio_id": TARGET_FOLIO_ID,
            "stay_ids": list(TARGET_STAY_IDS),
            "business_date": TARGET_BUSINESS_DATE.isoformat(),
            "current_business_date": EXPECTED_CURRENT_BUSINESS_DATE.isoformat(),
            "last_closed_business_date": EXPECTED_LAST_CLOSED_DATE.isoformat(),
            "transaction_ids": [row["transaction"].id for row in rows],
            "amount_reversed_total": str(EXPECTED_ROOM_CHARGE_AMOUNT * 2),
            "recorded_payment_total_before": str(payment_total),
            "pre_correction_folio_total": str(summary.total),
            "reason": CORRECTION_REASON,
        }
        snapshot_path = DATA_DIR / "reconciliation" / "furqan_2026-09-23_overaccrued_room_nights.json"
        snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        print(f"Audit snapshot: {snapshot_path}")

        print(
            "\nCorrection plan: reverse exactly the two 2026-09-23 room charges "
            "for stays #40 and #41. No payment is created, deleted, or changed."
        )
        if not args.apply:
            print("\nDRY RUN ONLY — no database changes were made.")
            return 0

        for row in rows:
            reverse_transaction(
                db,
                transaction_id=row["transaction"].id,
                created_by=1,
                reason=CORRECTION_REASON,
                preserve_business_date=True,
            )
        db.commit()

        corrected = folio_ledger_summary(db, TARGET_FOLIO_ID)
        print("\nCorrection committed.")
        print(
            f"Folio #38 after correction: total={corrected.total:.2f}, "
            f"paid={corrected.paid:.2f}, balance={corrected.balance:.2f}"
        )
        if (
            corrected.total != EXPECTED_POST_CORRECTION_TOTAL
            or corrected.paid != EXPECTED_PAYMENT_TOTAL
            or corrected.balance != Decimal("0.00")
        ):
            raise RuntimeError("Post-correction verification failed.")
        print("Verified target folio is PKR 44,000 charged, PKR 44,000 paid, PKR 0 due.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
