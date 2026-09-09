from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from secrets import token_hex

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Table, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import Base, get_db
from .models import AuditLog, BusinessDateState, StockItem, StockMovement, User

router = APIRouter(prefix="/api/inventory", tags=["inventory"])
QTY = Decimal("0.001")
MONEY = Decimal("0.01")

stock_operations = Table(
    "stock_operations",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("operation_no", String(40), unique=True, index=True, nullable=False),
    Column("idempotency_key", String(100), unique=True, index=True, nullable=False),
    Column("idempotency_fingerprint", String(64), index=True, nullable=False),
    Column("business_date", Date, index=True, nullable=False),
    Column("operation_type", String(30), index=True, nullable=False),
    Column("stock_item_id", Integer, ForeignKey("stock_items.id"), index=True, nullable=False),
    Column("quantity", Numeric(14, 3), nullable=False),
    Column("unit_cost", Numeric(12, 2), nullable=False),
    Column("reason", String(300), nullable=False),
    Column("created_by", Integer, ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False),
)


class StockOperationResponse(BaseModel):
    id: int
    operation_no: str
    idempotency_key: str
    business_date: date
    operation_type: str
    stock_item_id: int
    quantity: Decimal
    unit_cost: Decimal
    reason: str
    on_hand_after: Decimal
    replayed: bool = False


class ReceiveRequest(BaseModel):
    stock_item_id: int
    quantity: Decimal = Field(gt=0)
    unit_cost: Decimal = Field(default=Decimal("0.00"), ge=0)
    reason: str = Field(default="Stock received", min_length=1, max_length=300)


class AdjustmentRequest(BaseModel):
    stock_item_id: int
    quantity: Decimal
    reason: str = Field(default="Stock adjustment", min_length=1, max_length=300)


class ReconciliationResponse(BaseModel):
    stock_item_id: int
    sku: str
    name: str
    live_on_hand: Decimal
    movement_total: Decimal
    variance: Decimal
    reconciled: bool


def qty(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(QTY, rounding=ROUND_HALF_UP)


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def fingerprint(operation_type: str, stock_item_id: int, quantity: Decimal, unit_cost: Decimal, reason: str, business_date: date) -> str:
    payload = {
        "operation_type": operation_type,
        "stock_item_id": stock_item_id,
        "quantity": str(qty(quantity)),
        "unit_cost": str(money(unit_cost)),
        "reason": reason[:300],
        "business_date": business_date.isoformat(),
    }
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def lock_business_date(db: Session) -> date:
    state = db.scalar(select(BusinessDateState).where(BusinessDateState.id == 1).with_for_update())
    if state is None:
        raise HTTPException(status_code=500, detail="Business date is not initialized")
    return state.current_business_date


def build_operation_response(row, stock: StockItem, expected_fingerprint: str, replayed: bool) -> dict:
    if row["idempotency_fingerprint"] != expected_fingerprint:
        raise HTTPException(status_code=409, detail="Idempotency key is already bound to a different inventory operation")
    return {
        "id": row["id"],
        "operation_no": row["operation_no"],
        "idempotency_key": row["idempotency_key"],
        "business_date": row["business_date"],
        "operation_type": row["operation_type"],
        "stock_item_id": row["stock_item_id"],
        "quantity": row["quantity"],
        "unit_cost": row["unit_cost"],
        "reason": row["reason"],
        "on_hand_after": qty(stock.on_hand),
        "replayed": replayed,
    }


def audit(db: Session, user_id: int, action: str, operation_id: int, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type="stock_operation", entity_id=str(operation_id), details=json.dumps(details)))


def find_existing(db: Session, key: str):
    return db.execute(select(stock_operations).where(stock_operations.c.idempotency_key == key).with_for_update()).mappings().first()


def create_operation(db: Session, user: User, stock: StockItem, business_date: date, operation_type: str, quantity: Decimal, unit_cost: Decimal, reason: str, idempotency_key: str) -> dict:
    expected = fingerprint(operation_type, stock.id, quantity, unit_cost, reason, business_date)
    existing = find_existing(db, idempotency_key)
    if existing is not None:
        return build_operation_response(existing, stock, expected, True)

    quantity = qty(quantity)
    unit_cost = money(unit_cost)
    if quantity == 0:
        raise HTTPException(status_code=400, detail="Inventory operation quantity cannot be zero")
    if not stock.active:
        raise HTTPException(status_code=409, detail="Stock item is inactive")

    new_on_hand = qty(stock.on_hand + quantity)
    if new_on_hand < 0:
        raise HTTPException(status_code=409, detail=f"Inventory operation would make on-hand negative: {new_on_hand}")

    stock.on_hand = new_on_hand
    result = db.execute(
        insert(stock_operations).values(
            operation_no=f"STK-{business_date.strftime('%Y%m%d')}-{token_hex(4).upper()}",
            idempotency_key=idempotency_key,
            idempotency_fingerprint=expected,
            business_date=business_date,
            operation_type=operation_type,
            stock_item_id=stock.id,
            quantity=quantity,
            unit_cost=unit_cost,
            reason=reason[:300],
            created_by=user.id,
        ).returning(stock_operations)
    ).mappings().one()
    movement_type = "receipt" if operation_type == "receipt" else ("adjustment_in" if quantity > 0 else "adjustment_out")
    db.add(StockMovement(stock_item_id=stock.id, business_date=business_date, quantity=quantity, movement_type=movement_type, reference_type="stock_operation", reference_id=str(result["id"]), unit_cost=unit_cost, created_by=user.id))
    audit(db, user.id, operation_type, result["id"], {"stock_item_id": stock.id, "quantity": str(quantity), "unit_cost": str(unit_cost), "reason": reason[:300], "business_date": str(business_date)})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = find_existing(db, idempotency_key)
        if existing is not None:
            stock = db.get(StockItem, existing["stock_item_id"])
            return build_operation_response(existing, stock, expected, True)
        raise HTTPException(status_code=409, detail="Inventory operation could not be committed") from exc
    stock = db.get(StockItem, stock.id)
    return build_operation_response(result, stock, expected, False)


@router.get("/stock-items")
def list_stock_items(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    return db.scalars(select(StockItem).order_by(StockItem.name)).all()


@router.get("/movements")
def list_movements(stock_item_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    stmt = select(StockMovement).order_by(StockMovement.id.desc())
    if stock_item_id is not None:
        stmt = stmt.where(StockMovement.stock_item_id == stock_item_id)
    return db.scalars(stmt.limit(500)).all()


@router.get("/stock-items/{stock_item_id}/reconciliation", response_model=ReconciliationResponse)
def reconcile_stock_item(stock_item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    stock = db.get(StockItem, stock_item_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock item not found")
    movement_total = qty(db.scalar(select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(StockMovement.stock_item_id == stock.id)) or 0)
    live = qty(stock.on_hand)
    variance = qty(live - movement_total)
    return ReconciliationResponse(stock_item_id=stock.id, sku=stock.sku, name=stock.name, live_on_hand=live, movement_total=movement_total, variance=variance, reconciled=variance == Decimal("0.000"))


@router.post("/receive", response_model=StockOperationResponse, status_code=201)
def receive_stock(payload: ReceiveRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    key = (idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required for stock receipt")
    business_date = lock_business_date(db)
    stock = db.scalar(select(StockItem).where(StockItem.id == payload.stock_item_id).with_for_update())
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock item not found")
    return create_operation(db, user, stock, business_date, "receipt", payload.quantity, payload.unit_cost, payload.reason, key)


@router.post("/adjust", response_model=StockOperationResponse, status_code=201)
def adjust_stock(payload: AdjustmentRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    key = (idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required for stock adjustment")
    business_date = lock_business_date(db)
    stock = db.scalar(select(StockItem).where(StockItem.id == payload.stock_item_id).with_for_update())
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock item not found")
    return create_operation(db, user, stock, business_date, "adjustment", payload.quantity, Decimal("0.00"), payload.reason, key)
