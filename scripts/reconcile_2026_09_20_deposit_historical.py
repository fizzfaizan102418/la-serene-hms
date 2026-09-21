from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.db import DATA_DIR, SessionLocal
from app.models import BusinessDateState, FinancialTransaction, LedgerEntry

TARGET_DATE = date(2026, 9, 20)
EXPECTED_DEPOSIT = Decimal("25000.00")
MARKER = "historical-deposit-correction:2026-09-20:25000"


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def fail(message: str) -> None:
    raise RuntimeError(message)


def main() -> None:
    db = SessionLocal()
    try:
        state = db.get(BusinessDateState, 1)
        if state is None:
            fail("Business date state is missing")
        if state.current_business_date != date(2026, 9, 21):
            fail(f"Safety stop: expected current business date 2026-09-21, found {state.current_business_date}")
        if state.last_closed_business_date != TARGET_DATE:
            fail(f"Safety stop: expected 2026-09-20 to be the last closed business date, found {state.last_closed_business_date}")

        pack_path = DATA_DIR / "daily_closing" / TARGET_DATE.isoformat() / "daily-closing.json"
        if not pack_path.exists():
            fail(f"Safety stop: historical closing pack not found: {pack_path}")

        payload = json.loads(pack_path.read_text(encoding="utf-8"))
        report = payload.get("report") or {}
        if report.get("business_date") != TARGET_DATE.isoformat():
            fail("Safety stop: closing pack business date does not match 2026-09-20")

        reconciliation = report.get("historical_reconciliation") or {}
        if reconciliation.get("correction_marker") == MARKER:
            print("Already corrected; no changes made.")
            return

        # Only use authoritative posted ledger transactions. Deposits are liabilities,
        # but their debit to a payment account is a genuine cashier receipt.
        rows = db.execute(
            select(
                LedgerEntry.payment_method,
                func.coalesce(func.sum(LedgerEntry.amount), 0),
            )
            .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
            .where(
                FinancialTransaction.business_date == TARGET_DATE,
                FinancialTransaction.status == "posted",
                FinancialTransaction.transaction_type == "deposit_received",
                LedgerEntry.direction == "debit",
                LedgerEntry.account.in_(("Cash", "Card Clearing", "Bank", "Other Payment")),
            )
            .group_by(LedgerEntry.payment_method)
        ).all()

        deposit_by_method = {(method or "other"): money(amount) for method, amount in rows}
        deposit_total = money(sum(deposit_by_method.values(), Decimal("0.00")))
        if deposit_total != EXPECTED_DEPOSIT:
            fail(
                f"Safety stop: authoritative 2026-09-20 deposit total is {deposit_total}, "
                f"expected {EXPECTED_DEPOSIT}; methods={deposit_by_method}"
            )

        old_occupancy = json.dumps(report.get("occupancy"), sort_keys=True)
        old_outstanding = money(report.get("outstanding", "0.00"))

        payments = report.setdefault("payments", {})
        old_total = money(payments.get("total", "0.00"))
        for method, amount in deposit_by_method.items():
            payments[method] = money(payments.get(method, "0.00")) + amount
        payments["total"] = money(old_total + deposit_total)

        # Deposit receipt is not revenue. Preserve revenue, occupancy, and outstanding.
        guest_deposits = report.setdefault("guest_deposits", {})
        guest_deposits["received_today"] = money(guest_deposits.get("received_today", "0.00")) + deposit_total

        finance = report.get("finance") or {}
        payment_recon = finance.get("payment_reconciliation") or {}
        payment_recon["received_total"] = money(payment_recon.get("received_total", "0.00")) + deposit_total
        payment_recon["net_total"] = money(payment_recon.get("net_total", "0.00")) + deposit_total
        for row in payment_recon.get("methods", []):
            method = row.get("method")
            if method in deposit_by_method:
                row["received"] = money(row.get("received", "0.00")) + deposit_by_method[method]
                row["net"] = money(row.get("net", "0.00")) + deposit_by_method[method]
        for method, amount in deposit_by_method.items():
            if not any(row.get("method") == method for row in payment_recon.get("methods", [])):
                payment_recon.setdefault("methods", []).append({
                    "method": method,
                    "received": amount,
                    "refunded": Decimal("0.00"),
                    "net": amount,
                })
        finance["payment_reconciliation"] = payment_recon
        report["finance"] = finance

        # Keep the closed historical operating snapshot immutable.
        if json.dumps(report.get("occupancy"), sort_keys=True) != old_occupancy:
            fail("Safety stop: occupancy changed during correction")
        if money(report.get("outstanding", "0.00")) != old_outstanding:
            fail("Safety stop: outstanding changed during correction")

        reconciliation.update({
            "correction_marker": MARKER,
            "correction_type": "controlled_historical_pack_update",
            "deposit_business_date": TARGET_DATE.isoformat(),
            "deposit_amount": str(deposit_total),
            "deposit_by_method": {k: str(v) for k, v in deposit_by_method.items()},
            "revenue_changed": False,
            "occupancy_changed": False,
            "outstanding_changed": False,
            "source": "authoritative posted financial ledger",
        })
        report["historical_reconciliation"] = reconciliation

        pack_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(json.dumps({
            "status": "corrected",
            "business_date": TARGET_DATE.isoformat(),
            "deposit_total": str(deposit_total),
            "deposit_by_method": {k: str(v) for k, v in deposit_by_method.items()},
            "occupancy_preserved": True,
            "outstanding_preserved": str(old_outstanding),
            "revenue_changed": False,
            "pack": str(pack_path),
        }, indent=2))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
