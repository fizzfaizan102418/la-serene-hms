from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.db import DATA_DIR, SessionLocal
from app.financial_authority import folio_ledger_summary
from app.ledger import reverse_transaction
from app.models import BusinessDateState, FinancialTransaction, Folio, FolioItem, Guest, LedgerEntry, Payment, Reservation
from app.pms_core import Stay

TARGET_RESERVATION_ID = 38
TARGET_FOLIO_ID = 38
TARGET_STAY_ID = 40
TARGET_BUSINESS_DATE = date(2026, 9, 22)
EXPECTED_CURRENT_BUSINESS_DATE = date(2026, 9, 23)
EXPECTED_AMOUNT = Decimal("10000.00")
EXPECTED_ROOM_COUNT = 2
CORRECTION_REASON = "Duplicate room charge correction: stay #40 room 1 night 2026-09-22"


def _room_charge_transactions(db):
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
        ).all()      room_credit = next(
            (
                entry
                for entry in entries
                if entry.account == "Revenue - room" and entry.direction == "credit"
            ),
            None,
        )
        if room_credit is None:
            continue

        item = None
        if tx.reference_type == "folio_item" and tx.reference_id and tx.reference_id.isdigit():
            item = db.get(FolioItem, int(tx.reference_id))

        matches_stay = room_credit.stay_id == TARGET_STAY_ID or (
            item is not None and item.stay_id == TARGET_STAY_ID
        )
        if not matches_stay:
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
                "item": item,
                "amount": Decimal(amount),
                "idempotency_key": tx.idempotency_key,
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
    if state.last_closed_business_date != TARGET_BUSINESS_DATE:
        raise RuntimeError(
            f"Safety stop: last closed business date is {state.last_closed_business_date}, "
            f"expected {TARGET_BUSINESS_DATE}."
        )

    reservation = db.get(Reservation, TARGET_RESERVATION_ID)
    folio = db.get(Folio, TARGET_FOLIO_ID)
    stay = db.get(Stay, TARGET_STAY_ID)
    if reservation is None or folio is None or stay is None:
        raise RuntimeError("Safety stop: target reservation, folio, or stay was not found.")
    if folio.reservation_id != reservation.id or stay.reservation_id != reservation.id:
        raise RuntimeError("Safety stop: target folio/stay does not belong to reservation #38.")

    guest = db.get(Guest, reservation.guest_id)
    print(f"Guest: {guest.full_name if guest else 'unknown'}")
    print(f"Reservation: #{reservation.id}")
    print(f"Folio: #{folio.id}")
    print(f"Stay: #{stay.id} room_id={stay.room_id} {stay.check_in} -> {stay.check_out}")
    return state, reservation, folio, stay


def _print_payments(db, folio_id: int):
    payments = db.scalars(
        select(Payment).where(Payment.folio_id == folio_id).order_by(Payment.id)
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
        description="Controlled correction for the confirmed duplicate 2026-09-22 room charge on M. Furqan folio #38."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the validated correction. Without this flag the script is read-only.",
    )
    args = parser.parse_args()

    DATA_DIR.joinpath("reconciliation").mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        _validate_scope(db)
        payment_total = _print_payments(db, TARGET_FOLIO_ID)
        rows = _room_charge_transactions(db)

        print("\nMatching posted room-night transactions:")
        for row in rows:
            tx = row["transaction"]
            item = row["item"]
            print(
                f"  TX #{tx.id} | amount={row['amount']:.2f} | "
                f"item={item.id if item else 'unresolved'} | "
                f"idempotency={row['idempotency_key'] or 'none'}"
            )

        if len(rows) == 1:
            print("\nNo duplicate remains. Exactly one posted room charge matches stay #40 on 2026-09-22.")
            summary = folio_ledger_summary(db, TARGET_FOLIO_ID)
            print(
                f"Current folio totals: total={summary.total:.2f}, "
                f"paid={summary.paid:.2f}, balance={summary.balance:.2f}"
            )
            return 0

        if len(rows) != EXPECTED_ROOM_COUNT:
            raise RuntimeError(
                f"Safety stop: expected exactly {EXPECTED_ROOM_COUNT} posted matching room charges; "
                f"found {len(rows)}."
            )
        if any(row["amount"] != EXPECTED_AMOUNT for row in rows):
            raise RuntimeError("Safety stop: duplicate amounts are not both exactly PKR 10,000.00.")

        already_reversed = []
        for row in rows:
            tx_id = row["transaction"].id
            reversal = db.scalar(
                select(FinancialTransaction.id)
                .where(FinancialTransaction.reversal_of_id == tx_id)
                .limit(1)
            )
            if reversal is not None:
                already_reversed.append((tx_id, reversal))

        if already_reversed:
            if len(already_reversed) == 2:
                print("\nBoth matching transactions already have reversals; no change made.")
                return 0
            raise RuntimeError(
                f"Safety stop: one or more target transactions already have reversals: {already_reversed}"
            )

        # Prefer the deterministic transaction created by the fixed reconciler.
        deterministic_key = f"room-night:{TARGET_FOLIO_ID}:{TARGET_STAY_ID}:{TARGET_BUSINESS_DATE.isoformat()}"
        keeper = next(
            (row for row in rows if row["idempotency_key"] == deterministic_key),
            min(rows, key=lambda row: row["transaction"].id),
        )
        duplicate = next(row for row in rows if row["transaction"].id != keeper["transaction"].id)

        snapshot = {
            "correction": "duplicate_room_charge",
            "reservation_id": TARGET_RESERVATION_ID,
            "folio_id": TARGET_FOLIO_ID,
            "stay_id": TARGET_STAY_ID,
            "business_date": TARGET_BUSINESS_DATE.isoformat(),
            "current_business_date": EXPECTED_CURRENT_BUSINESS_DATE.isoformat(),
            "keeper_transaction_id": keeper["transaction"].id,
            "duplicate_transaction_id": duplicate["transaction"].id,
            "amount_reversed": str(EXPECTED_AMOUNT),
            "recorded_payment_total_before": str(payment_total),
        }
        snapshot_path = DATA_DIR / "reconciliation" / "duplicate_room_charge_2026-09-22.json"
        snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

        print(
            f"\nCorrection plan: keep TX #{keeper['transaction'].id}; "
            f"reverse duplicate TX #{duplicate['transaction'].id} "
            f"for PKR {EXPECTED_AMOUNT:.2f} on business date {TARGET_BUSINESS_DATE}."
        )
        print(f"Audit snapshot: {snapshot_path}")

        if not args.apply:
            print("\nDRY RUN ONLY — no database changes were made.")
            return 0

        reverse_transaction(
            db,
            transaction_id=duplicate["transaction"].id,
            created_by=1,
            reason=CORRECTION_REASON,
            preserve_business_date=True,
        )
        db.commit()

        summary = folio_ledger_summary(db, TARGET_FOLIO_ID)
        print("\nCorrection committed.")
        print(
            f"Folio #38 after correction: total={summary.total:.2f}, "
            f"paid={summary.paid:.2f}, balance={summary.balance:.2f}"
        )
        print(f"Recorded payments remain: {payment_total:.2f}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
