from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .auth import require_roles
from .business_date import get_current_business_date
from .db import get_db
from .models import AuditLog, User

router = APIRouter(prefix="/api/expenses", tags=["expenses"])
MONEY = Decimal("0.01")

CATEGORIES = [
    "Electricity", "Gas", "Internet", "Water", "Maintenance & repairs",
    "Cleaning supplies", "Housekeeping supplies", "Restaurant groceries",
    "Meat / poultry", "Vegetables", "Beverages", "Bakery / food supplies",
    "Restaurant/staff meals", "Laundry", "Transportation", "Staff wages / casual labor",
    "Hotel supplies", "Office expenses", "Telephone/mobile", "Equipment repair",
    "Government/licensing charges", "Miscellaneous",
]
DEPARTMENTS = ["Hotel", "Front Office", "Housekeeping", "Restaurant / Kitchen", "Maintenance", "Administration", "Other"]
PAYMENT_METHODS = ["Cash", "Bank", "Card", "Bank Transfer", "Other"]
STATUSES = ["posted", "voided"]


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


class ExpenseCreate(BaseModel):
    expense_date: date | None = None
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0)
    payment_method: str = Field(min_length=1, max_length=30)
    paid_to: str | None = Field(default=None, max_length=160)
    reference: str | None = Field(default=None, max_length=100)
    department: str = Field(default="Hotel", max_length=60)
    notes: str | None = Field(default=None, max_length=1000)


def _row(row) -> dict:
    return {
        "id": row.id,
        "expense_no": row.expense_no or f"EXP-{row.id:06d}",
        "expense_date": row.expense_date,
        "category": row.category or "Miscellaneous",
        "description": row.description,
        "amount": money(row.amount),
        "payment_method": row.payment_method,
        "paid_to": row.paid_to,
        "reference": row.reference,
        "department": row.department or "Hotel",
        "notes": row.notes,
        "created_by": row.created_by,
        "status": row.status or "posted",
        "created_at": row.created_at,
    }


@router.get("/meta")
def expense_meta(_: User = Depends(require_roles("admin", "reception"))):
    return {"categories": CATEGORIES, "departments": DEPARTMENTS, "payment_methods": PAYMENT_METHODS, "statuses": STATUSES}


@router.get("")
def list_expenses(
    from_date: date | None = None,
    to_date: date | None = None,
    category: str | None = Query(default=None, max_length=80),
    department: str | None = Query(default=None, max_length=60),
    payment_method: str | None = Query(default=None, max_length=30),
    status: str = Query(default="posted", max_length=20),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "reception")),
):
    conditions = ["e.status = :status"]
    params: dict[str, object] = {"status": status}
    if from_date:
        conditions.append("e.expense_date >= :from_date"); params["from_date"] = from_date
    if to_date:
        conditions.append("e.expense_date <= :to_date"); params["to_date"] = to_date
    if category:
        conditions.append("e.category = :category"); params["category"] = category
    if department:
        conditions.append("e.department = :department"); params["department"] = department
    if payment_method:
        conditions.append("e.payment_method = :payment_method"); params["payment_method"] = payment_method
    stmt = text(f"SELECT e.id, e.expense_no, e.expense_date, e.category, e.description, e.amount, e.payment_method, e.paid_to, e.reference, e.department, e.notes, e.created_by, e.status, e.created_at FROM expenses e WHERE {' AND '.join(conditions)} ORDER BY e.expense_date DESC, e.id DESC")
    return [_row(row) for row in db.execute(stmt, params).mappings().all()]


@router.post("", status_code=201)
def create_expense(payload: ExpenseCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    expense_date = payload.expense_date or get_current_business_date(db, fallback_to_today=True)
    category = payload.category.strip()
    department = payload.department.strip()
    payment_method = payload.payment_method.strip()
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unsupported expense category")
    if department not in DEPARTMENTS:
        raise HTTPException(status_code=400, detail="Unsupported expense department")
    if payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail="Unsupported payment method")
    amount = money(payload.amount)
    result = db.execute(
        text("""INSERT INTO expenses (description, amount, payment_method, expense_date, expense_no, category, paid_to, reference, department, notes, created_by, status)
               VALUES (:description, :amount, :payment_method, :expense_date, NULL, :category, :paid_to, :reference, :department, :notes, :created_by, 'posted')
               RETURNING id"""),
        {"description": payload.description.strip(), "amount": amount, "payment_method": payment_method, "expense_date": expense_date, "category": category, "paid_to": payload.paid_to.strip() if payload.paid_to else None, "reference": payload.reference.strip() if payload.reference else None, "department": department, "notes": payload.notes.strip() if payload.notes else None, "created_by": user.id},
    )
    expense_id = int(result.scalar_one())
    expense_no = f"EXP-{expense_id:06d}"
    db.execute(text("UPDATE expenses SET expense_no = :expense_no WHERE id = :id"), {"expense_no": expense_no, "id": expense_id})
    db.add(AuditLog(user_id=user.id, action="create", entity_type="expense", entity_id=str(expense_id), details=f"expense_no={expense_no}; category={category}; department={department}; amount={amount}"))
    db.commit()
    row = db.execute(text("SELECT e.id, e.expense_no, e.expense_date, e.category, e.description, e.amount, e.payment_method, e.paid_to, e.reference, e.department, e.notes, e.created_by, e.status, e.created_at FROM expenses e WHERE e.id = :id"), {"id": expense_id}).mappings().one()
    return _row(row)


@router.patch("/{expense_id}/void")
def void_expense(expense_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    row = db.execute(text("SELECT id, status FROM expenses WHERE id = :id"), {"id": expense_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Expense not found")
    if row["status"] == "voided":
        return {"id": expense_id, "status": "voided"}
    db.execute(text("UPDATE expenses SET status = 'voided' WHERE id = :id"), {"id": expense_id})
    db.add(AuditLog(user_id=user.id, action="void", entity_type="expense", entity_id=str(expense_id), details="expense voided"))
    db.commit()
    return {"id": expense_id, "status": "voided"}


@router.get("/summary")
def expense_summary(
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "reception")),
):
    start = from_date or get_current_business_date(db, fallback_to_today=True)
    end = to_date or start
    if end < start:
        raise HTTPException(status_code=400, detail="to_date must be on or after from_date")
    params = {"from_date": start, "to_date": end}
    total = db.execute(text("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE status='posted' AND expense_date BETWEEN :from_date AND :to_date"), params).scalar_one()
    by_category = db.execute(text("SELECT category, COALESCE(SUM(amount),0) AS amount FROM expenses WHERE status='posted' AND expense_date BETWEEN :from_date AND :to_date GROUP BY category ORDER BY amount DESC"), params).mappings().all()
    by_department = db.execute(text("SELECT department, COALESCE(SUM(amount),0) AS amount FROM expenses WHERE status='posted' AND expense_date BETWEEN :from_date AND :to_date GROUP BY department ORDER BY amount DESC"), params).mappings().all()
    by_payment = db.execute(text("SELECT payment_method, COALESCE(SUM(amount),0) AS amount FROM expenses WHERE status='posted' AND expense_date BETWEEN :from_date AND :to_date GROUP BY payment_method ORDER BY amount DESC"), params).mappings().all()
    by_day = db.execute(text("SELECT expense_date, COALESCE(SUM(amount),0) AS amount FROM expenses WHERE status='posted' AND expense_date BETWEEN :from_date AND :to_date GROUP BY expense_date ORDER BY expense_date"), params).mappings().all()
    return {
        "from_date": start, "to_date": end, "total": money(total),
        "by_category": [{"category": row["category"], "amount": money(row["amount"])} for row in by_category],
        "by_department": [{"department": row["department"], "amount": money(row["amount"])} for row in by_department],
        "by_payment_method": [{"payment_method": row["payment_method"], "amount": money(row["amount"])} for row in by_payment],
        "daily": [{"date": row["expense_date"], "amount": money(row["amount"])} for row in by_day],
    }
