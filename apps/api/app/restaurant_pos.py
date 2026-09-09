from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from secrets import token_hex

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import require_roles
from .business_date import get_current_business_date
from .db import get_db
from .ledger import post_transaction, reverse_transaction
from .models import (
    AuditLog,
    Folio,
    FinancialTransaction,
    Guest,
    LedgerEntry,
    MenuItem,
    Payment,
    Reservation,
    RestaurantOrder,
    RestaurantOrderItem,
    StockItem,
    StockMovement,
    User,
)
from .financial_authority import folio_ledger_summary

router = APIRouter(prefix="/api/restaurant", tags=["restaurant-pos"])
MONEY = Decimal("0.01")
QTY = Decimal("0.001")


class MenuItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="food", min_length=1, max_length=50)
    unit_price: Decimal = Field(ge=0)
    active: bool = True
    stock_item_id: int | None = None
    stock_quantity_per_unit: Decimal = Field(default=Decimal("0"), ge=0)


class MenuItemResponse(MenuItemCreate):
    id: int

    class Config:
        from_attributes = True


class StockItemCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    unit: str = Field(default="unit", min_length=1, max_length=20)
    opening_quantity: Decimal = Field(default=Decimal("0"), ge=0)


class StockItemResponse(BaseModel):
    id: int
    sku: str
    name: str
    unit: str
    on_hand: Decimal
    active: bool


class RestaurantOrderCreate(BaseModel):
    folio_id: int


class RestaurantOrderItemCreate(BaseModel):
    menu_item_id: int
    quantity: Decimal = Field(gt=0)


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    method: str = Field(min_length=1, max_length=30)
    reference: str | None = Field(default=None, max_length=100)


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def qty(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(QTY, rounding=ROUND_HALF_UP)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    import json

    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


def order_total(db: Session, order: RestaurantOrder) -> tuple[Decimal, Decimal, Decimal]:
    lines = db.scalars(select(RestaurantOrderItem).where(RestaurantOrderItem.order_id == order.id).order_by(RestaurantOrderItem.id)).all()
    subtotal = money(sum((Decimal(line.quantity) * Decimal(line.unit_price) for line in lines), Decimal("0")))
    service_charge = money(subtotal * Decimal(order.service_charge_rate))
    return subtotal, service_charge, money(subtotal + service_charge)


def order_response(db: Session, order: RestaurantOrder) -> dict:
    subtotal, service_charge, total = order_total(db, order)
    lines = db.execute(
        select(RestaurantOrderItem, MenuItem.name)
        .join(MenuItem, MenuItem.id == RestaurantOrderItem.menu_item_id)
        .where(RestaurantOrderItem.order_id == order.id)
        .order_by(RestaurantOrderItem.id)
    ).all()
    return {
        "id": order.id,
        "order_no": order.order_no,
        "folio_id": order.folio_id,
        "reservation_id": order.reservation_id,
        "business_date": order.business_date,
        "status": order.status,
        "service_charge_rate": order.service_charge_rate,
        "subtotal": subtotal,
        "service_charge": service_charge,
        "total": total,
        "items": [
            {
                "id": item.id,
                "menu_item_id": item.menu_item_id,
                "name": name,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "line_total": money(item.quantity * item.unit_price),
                "folio_item_id": item.folio_item_id,
                "reversed": item.reversed_at is not None,
            }
            for item, name in lines
        ],
    }


@router.get("/stock-items", response_model=list[StockItemResponse])
def list_stock_items(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "reception")),
):
    return db.scalars(select(StockItem).order_by(StockItem.name)).all()


@router.post("/stock-items", response_model=StockItemResponse, status_code=201)
def create_stock_item(
    payload: StockItemCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    if db.scalar(select(StockItem.id).where((StockItem.sku == payload.sku) | (StockItem.name == payload.name))):
        raise HTTPException(status_code=409, detail="Stock item SKU or name already exists")
    item = StockItem(sku=payload.sku, name=payload.name, unit=payload.unit, on_hand=qty(payload.opening_quantity))
    db.add(item)
    db.flush()
    if payload.opening_quantity:
        db.add(
            StockMovement(
                stock_item_id=item.id,
                business_date=get_current_business_date(db),
                quantity=qty(payload.opening_quantity),
                movement_type="opening",
                reference_type="stock_item",
                reference_id=str(item.id),
                unit_cost=Decimal("0.00"),
                created_by=user.id,
            )
        )
    audit(db, user.id, "create", "stock_item", item.id, {"sku": item.sku, "opening_quantity": str(item.on_hand)})
    db.commit()
    db.refresh(item)
    return item


@router.get("/menu-items", response_model=list[MenuItemResponse])
def list_menu_items(
    active_only: bool = True,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "reception")),
):
    stmt = select(MenuItem).order_by(MenuItem.name)
    if active_only:
        stmt = stmt.where(MenuItem.active.is_(True))
    return db.scalars(stmt).all()


@router.post("/menu-items", response_model=MenuItemResponse, status_code=201)
def create_menu_item(
    payload: MenuItemCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    if db.scalar(select(MenuItem.id).where(MenuItem.name == payload.name)):
        raise HTTPException(status_code=409, detail="Menu item already exists")
    if payload.stock_item_id and not db.get(StockItem, payload.stock_item_id):
        raise HTTPException(status_code=400, detail="Stock item does not exist")
    item = MenuItem(**payload.model_dump(), unit_price=money(payload.unit_price), stock_quantity_per_unit=qty(payload.stock_quantity_per_unit))
    db.add(item)
    db.flush()
    audit(db, user.id, "create", "menu_item", item.id, {"name": item.name, "unit_price": str(item.unit_price)})
    db.commit()
    db.refresh(item)
    return item


@router.post("/orders", status_code=201)
def create_order(
    payload: RestaurantOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "reception")),
):
    folio = db.get(Folio, payload.folio_id)
    if folio is None:
        raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is closed")
    reservation = db.get(Reservation, folio.reservation_id)
    if reservation is None:
        raise HTTPException(status_code=409, detail="Folio reservation is missing")
    if reservation.status != "checked_in":
        raise HTTPException(status_code=409, detail="Restaurant charges require a checked-in reservation")
    business_date = get_current_business_date(db)
    order = RestaurantOrder(
        order_no=f"POS-{business_date.strftime('%Y%m%d')}-{token_hex(4).upper()}",
        folio_id=folio.id,
        reservation_id=reservation.id,
        business_date=business_date,
        status="open",
        service_charge_rate=Decimal("0.10"),
        created_by=user.id,
    )
    db.add(order)
    db.flush()
    audit(db, user.id, "create", "restaurant_order", order.id, {"folio_id": folio.id, "business_date": str(business_date)})
    db.commit()
    db.refresh(order)
    return order_response(db, order)


@router.post("/orders/{order_id}/items", status_code=201)
def add_order_item(
    order_id: int,
    payload: RestaurantOrderItemCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "reception")),
):
    order = db.get(RestaurantOrder, order_id)
    menu = db.get(MenuItem, payload.menu_item_id)
    if order is None or menu is None:
        raise HTTPException(status_code=404, detail="Order or menu item not found")
    if order.status != "open":
        raise HTTPException(status_code=409, detail="Only open orders can be edited")
    if not menu.active:
        raise HTTPException(status_code=409, detail="Menu item is inactive")
    line = RestaurantOrderItem(
        order_id=order.id,
        menu_item_id=menu.id,
        description=menu.name,
        quantity=qty(payload.quantity),
        unit_price=money(menu.unit_price),
        stock_quantity_per_unit=qty(menu.stock_quantity_per_unit),
    )
    db.add(line)
    db.flush()
    audit(db, user.id, "add", "restaurant_order_item", line.id, {"order_id": order.id, "menu_item_id": menu.id, "quantity": str(line.quantity)})
    db.commit()
    return order_response(db, order)


@router.post("/orders/{order_id}/post")
def post_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "reception")),
):
    order = db.scalar(select(RestaurantOrder).where(RestaurantOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Restaurant order not found")
    if order.status == "posted":
        return order_response(db, order)
    if order.status != "open":
        raise HTTPException(status_code=409, detail="Only open orders can be posted")

    business_date = get_current_business_date(db)
    if order.business_date != business_date:
        raise HTTPException(status_code=409, detail="Restaurant order belongs to a closed business date")
    folio = db.scalar(select(Folio).where(Folio.id == order.folio_id).with_for_update())
    if folio is None or folio.status != "open":
        raise HTTPException(status_code=409, detail="Restaurant order folio is unavailable")
    reservation = db.get(Reservation, order.reservation_id)
    if reservation is None or reservation.status != "checked_in":
        raise HTTPException(status_code=409, detail="Restaurant order requires an active checked-in reservation")

    lines = db.scalars(select(RestaurantOrderItem).where(RestaurantOrderItem.order_id == order.id).order_by(RestaurantOrderItem.id)).all()
    if not lines:
        raise HTTPException(status_code=409, detail="Cannot post an empty restaurant order")

    subtotal = Decimal("0.00")
    for line in lines:
        if line.reversed_at is not None:
            raise HTTPException(status_code=409, detail="Order contains an already reversed line")
        subtotal += money(line.quantity * line.unit_price)

        if line.stock_quantity_per_unit > 0:
            stock = db.scalar(select(StockItem).where(StockItem.id == db.scalar(select(MenuItem.stock_item_id).where(MenuItem.id == line.menu_item_id))).with_for_update())
            if stock is None:
                raise HTTPException(status_code=409, detail=f"Stock item for {line.description} is missing")
            required = qty(line.quantity * line.stock_quantity_per_unit)
            if stock.on_hand < required:
                raise HTTPException(status_code=409, detail=f"Insufficient stock for {line.description}: required {required}, available {stock.on_hand}")
            stock.on_hand = qty(stock.on_hand - required)
            db.add(StockMovement(stock_item_id=stock.id, business_date=business_date, quantity=-required, movement_type="pos_sale", reference_type="restaurant_order", reference_id=str(order.id), unit_cost=Decimal("0.00"), created_by=user.id))

        folio_item = __import__("app.models", fromlist=["FolioItem"]).FolioItem(
            folio_id=folio.id,
            description=f"Restaurant · {line.description}",
            category="food",
            quantity=line.quantity,
            unit_price=line.unit_price,
            discount=Decimal("0.00"),
        )
        db.add(folio_item)
        db.flush()
        line.folio_item_id = folio_item.id
        post_transaction(
            db,
            transaction_type="folio_charge",
            description=f"Restaurant order {order.order_no} · {line.description}",
            reference_type="restaurant_order_item",
            reference_id=str(line.id),
            folio_id=folio.id,
            reservation_id=reservation.id,
            created_by=user.id,
            idempotency_key=f"restaurant-charge:{line.id}",
            lines=[
                {"account": "Guest Receivables", "direction": "debit", "amount": money(line.quantity * line.unit_price), "folio_id": folio.id},
                {"account": "Revenue - Food", "direction": "credit", "amount": money(line.quantity * line.unit_price), "folio_id": folio.id},
            ],
        )

    subtotal = money(subtotal)
    service_charge = money(subtotal * Decimal(order.service_charge_rate))
    if service_charge > 0:
        post_transaction(
            db,
            transaction_type="service_charge",
            description=f"Restaurant service charge · {order.order_no}",
            reference_type="restaurant_order",
            reference_id=str(order.id),
            folio_id=folio.id,
            reservation_id=reservation.id,
            created_by=user.id,
            idempotency_key=f"restaurant-service-charge:{order.id}",
            lines=[
                {"account": "Guest Receivables", "direction": "debit", "amount": service_charge, "folio_id": folio.id},
                {"account": "Revenue - service_charge", "direction": "credit", "amount": service_charge, "folio_id": folio.id},
            ],
        )
    order.status = "posted"
    order.posted_at = datetime.utcnow()
    audit(db, user.id, "post", "restaurant_order", order.id, {"subtotal": str(subtotal), "service_charge": str(service_charge), "total": str(subtotal + service_charge), "business_date": str(business_date)})
    db.commit()
    db.refresh(order)
    return order_response(db, order)


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: int,
    reason: str = "Cancelled before posting",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "reception")),
):
    order = db.scalar(select(RestaurantOrder).where(RestaurantOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Restaurant order not found")
    if order.status != "open":
        raise HTTPException(status_code=409, detail="Only unposted orders can be cancelled without financial reversal")
    order.status = "cancelled"
    order.void_reason = reason[:300]
    order.voided_at = datetime.utcnow()
    audit(db, user.id, "cancel", "restaurant_order", order.id, {"reason": order.void_reason})
    db.commit()
    return order_response(db, order)


@router.post("/orders/{order_id}/void")
def void_posted_order(
    order_id: int,
    reason: str = "Restaurant order void",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    order = db.scalar(select(RestaurantOrder).where(RestaurantOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Restaurant order not found")
    if order.status != "posted":
        raise HTTPException(status_code=409, detail="Only posted orders can be voided")
    business_date = get_current_business_date(db)
    if order.business_date > business_date:
        raise HTTPException(status_code=409, detail="Restaurant order belongs to a future business date")

    lines = db.scalars(select(RestaurantOrderItem).where(RestaurantOrderItem.order_id == order.id).order_by(RestaurantOrderItem.id)).all()
    for line in lines:
        if line.folio_item_id is None:
            raise HTTPException(status_code=409, detail=f"Posted restaurant line {line.id} has no folio charge")
        transactions = db.scalars(
            select(FinancialTransaction)
            .where(
                FinancialTransaction.reference_type == "restaurant_order_item",
                FinancialTransaction.reference_id == str(line.id),
                FinancialTransaction.status == "posted",
            )
            .order_by(FinancialTransaction.id)
        ).all()
        for tx in transactions:
            reverse_transaction(db, transaction_id=tx.id, created_by=user.id, reason=reason[:300])
        stock_item_id = db.scalar(select(MenuItem.stock_item_id).where(MenuItem.id == line.menu_item_id))
        if stock_item_id and line.stock_quantity_per_unit > 0:
            stock = db.scalar(select(StockItem).where(StockItem.id == stock_item_id).with_for_update())
            if stock is None:
                raise HTTPException(status_code=409, detail=f"Stock item for {line.description} is missing")
            restore = qty(line.quantity * line.stock_quantity_per_unit)
            stock.on_hand = qty(stock.on_hand + restore)
            db.add(StockMovement(stock_item_id=stock.id, business_date=business_date, quantity=restore, movement_type="pos_void", reference_type="restaurant_order", reference_id=str(order.id), unit_cost=Decimal("0.00"), created_by=user.id))
        line.reversed_at = datetime.utcnow()

    order_tx = db.scalar(
        select(FinancialTransaction).where(
            FinancialTransaction.reference_type == "restaurant_order",
            FinancialTransaction.reference_id == str(order.id),
            FinancialTransaction.transaction_type == "service_charge",
            FinancialTransaction.status == "posted",
        )
    )
    if order_tx:
        reverse_transaction(db, transaction_id=order_tx.id, created_by=user.id, reason=reason[:300])

    order.status = "voided"
    order.void_reason = reason[:300]
    order.voided_at = datetime.utcnow()
    audit(db, user.id, "void", "restaurant_order", order.id, {"reason": order.void_reason, "business_date": str(business_date)})
    db.commit()
    db.refresh(order)
    return order_response(db, order)


@router.get("/orders/{order_id}")
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "reception")),
):
    order = db.get(RestaurantOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Restaurant order not found")
    return order_response(db, order)


@router.post("/orders/{order_id}/payments", status_code=201)
def add_pos_payment(
    order_id: int,
    payload: PaymentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "reception")),
):
    key = (idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required for POS payments")
    order = db.scalar(select(RestaurantOrder).where(RestaurantOrder.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Restaurant order not found")
    if order.status != "posted":
        raise HTTPException(status_code=409, detail="Restaurant order must be posted before payment")
    folio = db.scalar(select(Folio).where(Folio.id == order.folio_id).with_for_update())
    if folio is None or folio.status != "open":
        raise HTTPException(status_code=409, detail="Order folio is unavailable")
    existing = db.scalar(select(FinancialTransaction).where(FinancialTransaction.idempotency_key == key))
    if existing is not None:
        if existing.transaction_type != "folio_payment" or existing.folio_id != folio.id:
            raise HTTPException(status_code=409, detail="Idempotency key is already bound to another financial operation")
        payment = db.get(Payment, int(existing.reference_id)) if existing.reference_id else None
        if payment is None:
            raise HTTPException(status_code=409, detail="Idempotent POS payment record is missing")
        return {"id": payment.id, "order_id": order.id, "folio_id": folio.id, "amount": payment.amount, "method": payment.method, "reference": payment.reference, "replayed": True, "balance": folio_ledger_summary(db, folio.id).balance}

    summary = folio_ledger_summary(db, folio.id)
    amount = money(payload.amount)
    if amount > summary.balance:
        raise HTTPException(status_code=409, detail=f"Payment exceeds outstanding folio balance of {summary.balance}")
    reservation = db.get(Reservation, folio.reservation_id)
    payment = Payment(folio_id=folio.id, amount=amount, method=payload.method, reference=payload.reference)
    db.add(payment)
    db.flush()
    try:
        post_transaction(
            db,
            transaction_type="folio_payment",
            description=f"POS payment #{payment.id} ({payload.method})",
            reference_type="payment",
            reference_id=str(payment.id),
            folio_id=folio.id,
            reservation_id=reservation.id if reservation else None,
            created_by=user.id,
            idempotency_key=key,
            lines=[
                {"account": {"cash": "Cash", "card": "Card Clearing", "bank_transfer": "Bank", "other": "Other Payment"}.get(payload.method, "Other Payment"), "direction": "debit", "amount": amount, "folio_id": folio.id, "payment_method": payload.method},
                {"account": "Guest Receivables", "direction": "credit", "amount": amount, "folio_id": folio.id, "payment_method": payload.method},
            ],
        )
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, user.id, "payment", "restaurant_order", order.id, {"payment_id": payment.id, "amount": str(amount), "method": payload.method})
    db.commit()
    db.refresh(payment)
    return {"id": payment.id, "order_id": order.id, "folio_id": folio.id, "amount": payment.amount, "method": payment.method, "reference": payment.reference, "replayed": False, "balance": folio_ledger_summary(db, folio.id).balance}
