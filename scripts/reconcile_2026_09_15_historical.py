from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.db import DATA_DIR, SessionLocal
from app.models import AuditLog, Expense, FinancialTransaction, LedgerEntry, Role, User
from app.night_audit import build_pre_close_preview, build_summary, create_pack, ledger_account_delta

TARGET_DATE = date(2026, 9, 15)
OPENING_DATE = date(2026, 9, 14)
OPENING_CASH = Decimal("45176.00")
TRANSPORT_AMOUNT = Decimal("600.00")
EXPECTED_EXISTING_EXPENSES = {
    ("Gas", Decimal("21000.00"), "Cash"),
    ("Vegetables", Decimal("1300.00"), "Cash"),
    ("Meat / poultry", Decimal("1800.00"), "Cash"),
    ("Restaurant groceries", Decimal("4250.00"), "Cash"),
}
OPENING_KEY = "historical-reconciliation:2026-09-15:opening-cash"
TRANSPORT_KEY = "historical-reconciliation:2026-09-15:transport-expense"


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def fail(message: str) -> None:
    raise RuntimeError(message)


def add_historical_tx(db, *, business_date: date, transaction_no: str, idempotency_key: str, transaction_type: str, description: str, reference_id: str, created_by: int, lines: list[tuple[str, str, Decimal]]) -> FinancialTransaction:
    existing = db.scalar(select(FinancialTransaction).where(FinancialTransaction.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.status != "posted":
            fail(f"Existing historical transaction {existing.id} is not posted")
        return existing

    if db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.transaction_no == transaction_no)):
        fail(f"Transaction number already exists: {transaction_no}")

    debit = money(sum((amount for _, direction, amount in lines if direction == "debit"), Decimal("0.00")))
    credit = money(sum((amount for _, direction, amount in lines if direction == "credit"), Decimal("0.00")))
    if debit != credit:
        fail(f"Unbalanced historical transaction {transaction_no}: debit={debit} credit={credit}")

    tx = FinancialTransaction(
        transaction_no=transaction_no,
        idempotency_key=idempotency_key,
        idempotency_fingerprint=None,
        business_date=business_date,
        transaction_type=transaction_type,
        status="posted",
        reference_type="historical_reconciliation",
        reference_id=reference_id,
        description=description,
        created_by=created_by,
    )
    db.add(tx)
    db.flush()
    for account, direction, amount in lines:
        db.add(
            LedgerEntry(
                transaction_id=tx.id,
                account=account,
                direction=direction,
                amount=money(amount),
                currency="PKR",
                reference=reference_id,
            )
        )
    db.flush()
    return tx


def expense_tx_for(db, expense_id: int) -> FinancialTransaction | None:
    return db.scalar(
        select(FinancialTransaction).where(
            FinancialTransaction.reference_type == "expense",
            FinancialTransaction.reference_id == str(expense_id),
            FinancialTransaction.transaction_type == "expense",
        )
    )


def backfill_existing_expense(db, expense: Expense, admin_id: int) -> str:
    existing = expense_tx_for(db, expense.id)
    if existing is not None:
        entries = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == existing.id)).all()
        cash_credit = sum((Decimal(e.amount) for e in entries if e.account == "Cash" and e.direction == "credit"), Decimal("0.00"))
        expense_debit = sum((Decimal(e.amount) for e in entries if e.account == f"Expense - {expense.category[:45]}" and e.direction == "debit"), Decimal("0.00"))
        if money(cash_credit) != money(expense.amount) or money(expense_debit) != money(expense.amount):
            fail(f"Existing ledger transaction for expense {expense.id} does not match its cash expense amount")
        return "existing"

    add_historical_tx(
        db,
        business_date=TARGET_DATE,
        transaction_no=f"HIST-{TARGET_DATE:%Y%m%d}-EXP{expense.id:04d}",
        idempotency_key=f"historical-reconciliation:2026-09-15:expense:{expense.id}",
        transaction_type="expense",
        description=f"Historical backfill {expense.expense_no}: {expense.category}",
        reference_id=str(expense.id),
        created_by=admin_id,
        lines=[
            (f"Expense - {expense.category[:45]}", "debit", money(expense.amount)),
            ("Cash", "credit", money(expense.amount)),
        ],
    )
    return "backfilled"


def main() -> None:
    db = SessionLocal()
    try:
        state = db.execute(select(__import__("app.models", fromlist=["BusinessDateState"]).BusinessDateState).where(__import__("app.models", fromlist=["BusinessDateState"]).BusinessDateState.id == 1)).scalar_one_or_none()
        if state is None:
            fail("Business date state is missing")
        if state.current_business_date != date(2026, 9, 16):
            fail(f"Safety stop: expected current business date 2026-09-16, found {state.current_business_date}")
        if state.last_closed_business_date != TARGET_DATE:
            fail(f"Safety stop: expected 2026-09-15 to be the last closed business date, found {state.last_closed_business_date}")

        admin = db.scalar(select(User).join(Role, Role.id == User.role_id).where(Role.name == "admin").order_by(User.id).limit(1))
        if admin is None:
            fail("No admin user found")

        existing_opening = ledger_account_delta(db, TARGET_DATE, {"Cash"}, True)
        if money(existing_opening) != Decimal("0.00"):
            fail(f"Safety stop: existing Cash opening balance before 2026-09-15 is {existing_opening}, not 0.00")

        existing_expenses = db.scalars(select(Expense).where(Expense.expense_date == TARGET_DATE, Expense.status == "posted")).all()
        actual_signature = {(e.category, money(e.amount), e.payment_method) for e in existing_expenses}
        if not EXPECTED_EXISTING_EXPENSES.issubset(actual_signature):
            fail(f"Safety stop: expected existing cash expenses were not found exactly. Found: {sorted(actual_signature, key=str)}")
        if money(sum((Decimal(e.amount) for e in existing_expenses), Decimal("0.00"))) != Decimal("28350.00"):
            fail("Safety stop: existing 2026-09-15 posted expense total is not exactly PKR 28,350.00")

        opening_tx = add_historical_tx(
            db,
            business_date=OPENING_DATE,
            transaction_no="HIST-20260914-OPENING",
            idempotency_key=OPENING_KEY,
            transaction_type="historical_opening_balance",
            description="Historical opening cash balance carried into 2026-09-15",
            reference_id="2026-09-15",
            created_by=admin.id,
            lines=[
                ("Cash", "debit", OPENING_CASH),
                ("Opening Balance Equity", "credit", OPENING_CASH),
            ],
        )

        backfilled = []
        for expense in sorted(existing_expenses, key=lambda e: e.id):
            result = backfill_existing_expense(db, expense, admin.id)
            backfilled.append({"id": expense.id, "expense_no": expense.expense_no, "category": expense.category, "amount": str(money(expense.amount)), "status": result})

        transport = db.scalar(
            select(Expense).where(
                Expense.expense_date == TARGET_DATE,
                Expense.status == "posted",
                Expense.category == "Transportation",
                Expense.description == "Transport Expense",
                Expense.amount == TRANSPORT_AMOUNT,
            )
        )
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
            add_historical_tx(
                db,
                business_date=TARGET_DATE,
                transaction_no="HIST-20260915-TRANSPORT",
                idempotency_key=TRANSPORT_KEY,
                transaction_type="expense",
                description="Historical backfill: Transport Expense",
                reference_id=str(transport.id),
                created_by=admin.id,
                lines=[
                    ("Expense - Transportation", "debit", TRANSPORT_AMOUNT),
                    ("Cash", "credit", TRANSPORT_AMOUNT),
                ],
            )
        else:
            tx = expense_tx_for(db, transport.id)
            if tx is None:
                add_historical_tx(
                    db,
                    business_date=TARGET_DATE,
                    transaction_no="HIST-20260915-TRANSPORT",
                    idempotency_key=TRANSPORT_KEY,
                    transaction_type="expense",
                    description="Historical backfill: Transport Expense",
                    reference_id=str(transport.id),
                    created_by=admin.id,
                    lines=[
                        ("Expense - Transportation", "debit", TRANSPORT_AMOUNT),
                        ("Cash", "credit", TRANSPORT_AMOUNT),
                    ],
                )

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
                    "pending_company_receivable": "36000.00",
                    "total_revenue": "36000.00",
                    "total_expenses": "28950.00",
                    "closing_cash": "16226.00",
                    "opening_transaction_id": opening_tx.id,
                    "backfilled_expenses": backfilled,
                    "transport_expense_id": transport.id,
                }),
            )
        )
        db.commit()

        summary = build_summary(db, TARGET_DATE)
        summary["pre_close"] = build_pre_close_preview(db, TARGET_DATE, summary)
        summary["historical_reconciliation"] = {
            "source": "old Excel daily closing",
            "opening_cash": money(OPENING_CASH),
            "cash_receipts": Decimal("0.00"),
            "online_receipts": Decimal("0.00"),
            "pending_company": Decimal("36000.00"),
            "total_revenue": money(summary["revenue"]["gross"]),
            "total_expenses": money(summary["expenses"]),
            "closing_cash": money(summary["pre_close"]["projected_close"]["cash"]),
        }

        pack_path = DATA_DIR / "daily_closing" / TARGET_DATE.isoformat() / "daily-closing.json"
        notes = "Historical reconciliation: opening cash PKR 45,176.00; cash receipts PKR 0.00; pending/company PKR 36,000.00; total revenue PKR 36,000.00; total cash expenses PKR 28,950.00; closing cash PKR 16,226.00. Existing 15-Sep cash expenses were backfilled into the financial ledger and the missing PKR 600 transport expense was added."
        closed_by = "admin"
        closed_at = datetime.utcnow()
        if pack_path.exists():
            try:
                old_pack = json.loads(pack_path.read_text(encoding="utf-8"))
                closed_by = old_pack.get("closing", {}).get("closed_by") or closed_by
                old_closed_at = old_pack.get("closing", {}).get("closed_at")
                if old_closed_at:
                    closed_at = datetime.fromisoformat(old_closed_at.replace("Z", "+00:00")).replace(tzinfo=None)
                notes = (old_pack.get("closing", {}).get("notes") or "").strip() + ("\n\n" if old_pack.get("closing", {}).get("notes") else "") + notes
            except Exception:
                pass
        pack = create_pack(summary, notes, closed_by, closed_at)

        print(json.dumps({
            "status": "reconciled",
            "business_date": TARGET_DATE.isoformat(),
            "opening_cash": str(summary["pre_close"]["opening"]["cash"]),
            "cash_receipts": str(summary["pre_close"]["activity"]["cash_received"]),
            "expenses": str(summary["expenses"]),
            "projected_closing_cash": str(summary["pre_close"]["projected_close"]["cash"]),
            "revenue": str(summary["revenue"]["gross"]),
            "outstanding": str(summary["outstanding"]),
            "finance_status": summary["finance"]["status"],
            "trial_balance": summary["finance"]["trial_balance"]["balanced"],
            "pack": pack,
        }, indent=2, default=str))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
