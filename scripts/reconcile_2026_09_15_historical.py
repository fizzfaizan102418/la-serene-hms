from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.db import DATA_DIR, SessionLocal
from app.models import AuditLog, BusinessDateState, Expense, Role, User
from app.night_audit import build_pre_close_preview, build_summary, create_pack

TARGET_DATE = date(2026, 9, 15)
OPENING_CASH = Decimal("45176.00")
EXPECTED_PENDING = Decimal("36000.00")
EXPECTED_REVENUE = Decimal("36000.00")
EXPECTED_EXPENSES = Decimal("28950.00")
EXPECTED_CLOSING_CASH = Decimal("16226.00")
TRANSPORT_AMOUNT = Decimal("600.00")

EXPECTED_EXISTING_EXPENSES = {
    ("Gas", Decimal("21000.00"), "Cash"),
    ("Vegetables", Decimal("1300.00"), "Cash"),
    ("Meat / poultry", Decimal("1800.00"), "Cash"),
    ("Restaurant groceries", Decimal("4250.00"), "Cash"),
}
TRANSPORT_KEY = "historical-reconciliation:2026-09-15:transport-expense"


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def fail(message: str) -> None:
    raise RuntimeError(message)


def expense_signature(expenses: list[Expense]) -> set[tuple[str, Decimal, str]]:
    return {(e.category, money(e.amount), e.payment_method) for e in expenses}


def main() -> None:
    db = SessionLocal()
    try:
        state = db.get(BusinessDateState, 1)
        if state is None:
            fail("Business date state is missing")
        if state.current_business_date != date(2026, 9, 16):
            fail(f"Safety stop: expected current business date 2026-09-16, found {state.current_business_date}")
        if state.last_closed_business_date != TARGET_DATE:
            fail(f"Safety stop: expected 2026-09-15 to be the last closed business date, found {state.last_closed_business_date}")

        admin = db.scalar(
            select(User)
            .join(Role, Role.id == User.role_id)
            .where(Role.name == "admin")
            .order_by(User.id)
            .limit(1)
        )
        if admin is None:
            fail("No admin user found")

        existing_expenses = db.scalars(
            select(Expense).where(
                Expense.expense_date == TARGET_DATE,
                Expense.status == "posted",
            )
        ).all()
        signature = expense_signature(existing_expenses)
        if not EXPECTED_EXISTING_EXPENSES.issubset(signature):
            fail(f"Safety stop: expected existing cash expenses were not found. Found: {sorted(signature, key=str)}")

        existing_total = money(sum((Decimal(e.amount) for e in existing_expenses), Decimal("0.00")))
        if existing_total not in (Decimal("28350.00"), EXPECTED_EXPENSES):
            fail(f"Safety stop: existing 2026-09-15 posted expense total is {existing_total}, expected 28350.00 or already-reconciled 28950.00")

        transport = db.scalar(
            select(Expense).where(
                Expense.expense_date == TARGET_DATE,
                Expense.status == "posted",
                Expense.category == "Transportation",
                Expense.description == "Transport Expense",
                Expense.amount == TRANSPORT_AMOUNT,
            )
        )
        transport_created = False
        if transport is None:
            transport = Expense(
                description="Transport Expense",
                amount=TRANSPORT_AMOUNT,
                payment_method="Cash",
                expense_date=TARGET_DATE,
                expense_no="HIST-EXP-20260915-TRANSPORT",
                category="Transportation",
                paid_to=None,
                reference="Historical Excel reconciliation 2026-09-15",
                department="Hotel",
                notes="Historical reconciliation to match the hotel's Excel daily closing record.",
                created_by=admin.id,
                status="posted",
            )
            db.add(transport)
            db.flush()
            transport_created = True

        db.add(
            AuditLog(
                user_id=admin.id,
                action="historical_reconciliation",
                entity_type="daily_closing",
                entity_id=TARGET_DATE.isoformat(),
                details=json.dumps({
                    "business_date": TARGET_DATE.isoformat(),
                    "source": "old Excel daily closing",
                    "opening_cash": str(OPENING_CASH),
                    "cash_receipts": "0.00",
                    "pending_company_receivable": str(EXPECTED_PENDING),
                    "total_revenue": str(EXPECTED_REVENUE),
                    "total_expenses": str(EXPECTED_EXPENSES),
                    "closing_cash": str(EXPECTED_CLOSING_CASH),
                    "transport_expense_id": transport.id,
                    "transport_created": transport_created,
                    "financial_ledger_backfill": False,
                    "reason": "Closed historical period is protected by the current-business-date financial posting guard; historical opening cash is represented in the archived closing pack rather than bypassing that guard.",
                }),
            )
        )
        db.commit()

        summary = build_summary(db, TARGET_DATE)
        summary["pre_close"] = build_pre_close_preview(db, TARGET_DATE, summary)

        # The historical Excel closing is the source of truth for the closed 2026-09-15 pack.
        # Do not create financial transactions dated in a closed period: PostgreSQL explicitly
        # rejects those postings. Current-period financial controls remain untouched.
        summary["pre_close"]["opening"]["cash"] = OPENING_CASH
        summary["pre_close"]["opening"]["outstanding"] = EXPECTED_PENDING
        summary["pre_close"]["activity"]["cash_received"] = Decimal("0.00")
        summary["pre_close"]["activity"]["payments_received"] = Decimal("0.00")
        summary["pre_close"]["activity"]["expenses"] = EXPECTED_EXPENSES
        summary["pre_close"]["projected_close"]["cash"] = EXPECTED_CLOSING_CASH
        summary["pre_close"]["projected_close"]["guest_receivables"] = EXPECTED_PENDING
        summary["pre_close"]["projected_close"]["outstanding"] = EXPECTED_PENDING
        summary["expenses"] = EXPECTED_EXPENSES
        summary["net_operating"] = money(summary["revenue"]["gross"] - EXPECTED_EXPENSES)
        summary["outstanding"] = EXPECTED_PENDING
        summary["historical_reconciliation"] = {
            "source": "old Excel daily closing",
            "opening_cash": OPENING_CASH,
            "cash_receipts": Decimal("0.00"),
            "online_receipts": Decimal("0.00"),
            "pending_company": EXPECTED_PENDING,
            "total_revenue": EXPECTED_REVENUE,
            "total_expenses": EXPECTED_EXPENSES,
            "closing_cash": EXPECTED_CLOSING_CASH,
            "financial_ledger_backfill": False,
        }

        pack_path = DATA_DIR / "daily_closing" / TARGET_DATE.isoformat() / "daily-closing.json"
        notes = (
            "Historical reconciliation to old Excel daily closing: "
            "opening cash PKR 45,176.00; cash receipts PKR 0.00; "
            "pending/company PKR 36,000.00; total revenue PKR 36,000.00; "
            "total expenses PKR 28,950.00; closing cash PKR 16,226.00. "
            "The missing PKR 600 Transport Expense was added as a historical expense record. "
            "No financial transaction dated in the closed period was created, because the database "
            "business-date guard correctly rejects historical financial postings."
        )
        closed_by = "admin"
        closed_at = datetime.utcnow()
        if pack_path.exists():
            try:
                old_pack = json.loads(pack_path.read_text(encoding="utf-8"))
                closed_by = old_pack.get("closing", {}).get("closed_by") or closed_by
                old_closed_at = old_pack.get("closing", {}).get("closed_at")
                if old_closed_at:
                    closed_at = datetime.fromisoformat(old_closed_at.replace("Z", "+00:00")).replace(tzinfo=None)
                old_notes = (old_pack.get("closing", {}).get("notes") or "").strip()
                if old_notes:
                    notes = old_notes + "\n\n" + notes
            except Exception:
                pass

        pack = create_pack(summary, notes, closed_by, closed_at)

        print(json.dumps({
            "status": "reconciled",
            "business_date": TARGET_DATE.isoformat(),
            "source": "old Excel daily closing",
            "opening_cash": str(OPENING_CASH),
            "cash_receipts": "0.00",
            "expenses": str(EXPECTED_EXPENSES),
            "projected_closing_cash": str(EXPECTED_CLOSING_CASH),
            "revenue": str(EXPECTED_REVENUE),
            "outstanding": str(EXPECTED_PENDING),
            "transport_expense_id": transport.id,
            "transport_created": transport_created,
            "financial_ledger_backfill": False,
            "pack": pack,
        }, indent=2, default=str))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
