from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from secrets import token_hex

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Table, insert, select, update
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import Base, get_db
from .models import AuditLog, StockItem, StockMovement, User
from .inventory import lock_business_date

router = APIRouter(prefix="/api/purchasing", tags=["purchasing"])
QTY = Decimal("0.001")
MONEY = Decimal("0.01")

suppliers = Table("suppliers", Base.metadata,
    Column("id", Integer, primary_key=True), Column("code", String(40), unique=True, index=True, nullable=False),
    Column("name", String(160), nullable=False), Column("contact_name", String(120)), Column("phone", String(40)),
    Column("email", String(160)), Column("address", String(400)), Column("active", Boolean, nullable=False, server_default="1"),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow), Column("updated_at", DateTime, nullable=False, default=datetime.utcnow))

purchase_orders = Table("purchase_orders", Base.metadata,
    Column("id", Integer, primary_key=True), Column("po_no", String(40), unique=True, index=True, nullable=False),
    Column("supplier_id", Integer, ForeignKey("suppliers.id"), index=True, nullable=False), Column("business_date", Date, index=True, nullable=False),
    Column("status", String(30), nullable=False), Column("notes", String(500)), Column("created_by", Integer, ForeignKey("users.id"), nullable=False),
    Column("approved_by", Integer, ForeignKey("users.id")), Column("approved_at", DateTime),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow), Column("updated_at", DateTime, nullable=False, default=datetime.utcnow))

purchase_order_lines = Table("purchase_order_lines", Base.metadata,
    Column("id", Integer, primary_key=True), Column("purchase_order_id", Integer, ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("stock_item_id", Integer, ForeignKey("stock_items.id"), index=True, nullable=False), Column("description", String(200), nullable=False),
    Column("ordered_quantity", Numeric(14, 3), nullable=False), Column("received_quantity", Numeric(14, 3), nullable=False, server_default="0"), Column("unit_cost", Numeric(12, 2), nullable=False))

goods_receipts = Table("goods_receipts", Base.metadata,
    Column("id", Integer, primary_key=True), Column("grn_no", String(40), unique=True, index=True, nullable=False),
    Column("purchase_order_id", Integer, ForeignKey("purchase_orders.id"), index=True, nullable=False), Column("supplier_id", Integer, ForeignKey("suppliers.id"), index=True, nullable=False),
    Column("business_date", Date, index=True, nullable=False), Column("idempotency_key", String(100), unique=True, index=True, nullable=False),
    Column("idempotency_fingerprint", String(64), nullable=False), Column("created_by", Integer, ForeignKey("users.id"), nullable=False),
    Column("received_at", DateTime, nullable=False, default=datetime.utcnow), Column("notes", String(500)))

goods_receipt_lines = Table("goods_receipt_lines", Base.metadata,
    Column("id", Integer, primary_key=True), Column("receipt_id", Integer, ForeignKey("goods_receipts.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("purchase_order_line_id", Integer, ForeignKey("purchase_order_lines.id"), index=True, nullable=False),
    Column("quantity", Numeric(14, 3), nullable=False), Column("unit_cost", Numeric(12, 2), nullable=False))


class SupplierCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    contact_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=400)


class POLineCreate(BaseModel):
    stock_item_id: int
    ordered_quantity: Decimal = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)
    description: str = Field(min_length=1, max_length=200)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    notes: str | None = Field(default=None, max_length=500)
    lines: list[POLineCreate] = Field(min_length=1, max_length=100)


class ReceiveLine(BaseModel):
    purchase_order_line_id: int
    quantity: Decimal = Field(gt=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)


class ReceiveRequest(BaseModel):
    lines: list[ReceiveLine] = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=500)


def qty(value) -> Decimal:
    return Decimal(str(value)).quantize(QTY, rounding=ROUND_HALF_UP)


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def rowdict(row) -> dict:
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details, default=str)))


def receipt_fingerprint(po_id: int, business_date: date, lines: list[ReceiveLine], notes: str | None) -> str:
    payload = {"purchase_order_id": po_id, "business_date": business_date.isoformat(),
        "lines": sorted([{"purchase_order_line_id": x.purchase_order_line_id, "quantity": str(qty(x.quantity)), "unit_cost": None if x.unit_cost is None else str(money(x.unit_cost))} for x in lines], key=lambda x: x["purchase_order_line_id"]),
        "notes": (notes or "")[:500]}
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_po(db: Session, po: dict) -> dict:
    data = dict(po)
    data["supplier"] = dict(db.execute(select(suppliers).where(suppliers.c.id == po["supplier_id"])).mappings().one())
    data["lines"] = [dict(r) for r in db.execute(select(purchase_order_lines).where(purchase_order_lines.c.purchase_order_id == po["id"]).order_by(purchase_order_lines.c.id)).mappings().all()]
    return data


@router.get("/suppliers")
def list_suppliers(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    return [dict(r) for r in db.execute(select(suppliers).order_by(suppliers.c.name)).mappings().all()]


@router.post("/suppliers", status_code=201)
def create_supplier(payload: SupplierCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    code = payload.code.strip().upper()
    if db.scalar(select(suppliers.c.id).where(suppliers.c.code == code)):
        raise HTTPException(status_code=409, detail="Supplier code already exists")
    row = db.execute(insert(suppliers).values(code=code, name=payload.name.strip(), contact_name=payload.contact_name, phone=payload.phone, email=payload.email, address=payload.address, active=True).returning(suppliers)).mappings().one()
    audit(db, user.id, "supplier_created", "supplier", row["id"], {"code": code})
    db.commit()
    return dict(row)


@router.get("/orders")
def list_purchase_orders(status: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    stmt = select(purchase_orders).order_by(purchase_orders.c.id.desc())
    if status: stmt = stmt.where(purchase_orders.c.status == status)
    return [build_po(db, rowdict(r)) for r in db.execute(stmt).all()]


@router.post("/orders", status_code=201)
def create_purchase_order(payload: PurchaseOrderCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    business_date = lock_business_date(db)
    supplier = db.execute(select(suppliers).where(suppliers.c.id == payload.supplier_id).with_for_update()).mappings().first()
    if supplier is None: raise HTTPException(status_code=404, detail="Supplier not found")
    if not supplier["active"]: raise HTTPException(status_code=409, detail="Supplier is inactive")
    ids = [line.stock_item_id for line in payload.lines]
    if len(ids) != len(set(ids)): raise HTTPException(status_code=400, detail="Each stock item may appear only once per purchase order")
    stocks = {s.id: s for s in db.scalars(select(StockItem).where(StockItem.id.in_(ids)).with_for_update()).all()}
    if len(stocks) != len(ids): raise HTTPException(status_code=404, detail="One or more stock items were not found")
    if any(not s.active for s in stocks.values()): raise HTTPException(status_code=409, detail="Purchase orders cannot contain inactive stock items")
    po = db.execute(insert(purchase_orders).values(po_no=f"PO-{business_date.strftime('%Y%m%d')}-{token_hex(4).upper()}", supplier_id=payload.supplier_id, business_date=business_date, status="draft", notes=payload.notes, created_by=user.id).returning(purchase_orders)).mappings().one()
    for line in payload.lines:
        db.execute(insert(purchase_order_lines).values(purchase_order_id=po["id"], stock_item_id=line.stock_item_id, description=line.description.strip(), ordered_quantity=qty(line.ordered_quantity), received_quantity=Decimal("0.000"), unit_cost=money(line.unit_cost)))
    audit(db, user.id, "purchase_order_created", "purchase_order", po["id"], {"supplier_id": payload.supplier_id, "line_count": len(payload.lines), "business_date": str(business_date)})
    db.commit()
    return build_po(db, po)


@router.get("/orders/{po_id}")
def get_purchase_order(po_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    po = db.execute(select(purchase_orders).where(purchase_orders.c.id == po_id)).mappings().first()
    if po is None: raise HTTPException(status_code=404, detail="Purchase order not found")
    return build_po(db, dict(po))


@router.post("/orders/{po_id}/approve")
def approve_purchase_order(po_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    business_date = lock_business_date(db)
    po = db.execute(select(purchase_orders).where(purchase_orders.c.id == po_id).with_for_update()).mappings().first()
    if po is None: raise HTTPException(status_code=404, detail="Purchase order not found")
    if po["business_date"] != business_date: raise HTTPException(status_code=409, detail="Purchase order belongs to a different business date")
    if po["status"] != "draft": raise HTTPException(status_code=409, detail="Only draft purchase orders can be approved")
    db.execute(update(purchase_orders).where(purchase_orders.c.id == po_id).values(status="approved", approved_by=user.id, approved_at=datetime.utcnow(), updated_at=datetime.utcnow()))
    audit(db, user.id, "purchase_order_approved", "purchase_order", po_id, {"business_date": str(business_date)})
    db.commit()
    return build_po(db, dict(db.execute(select(purchase_orders).where(purchase_orders.c.id == po_id)).mappings().one()))


@router.post("/orders/{po_id}/cancel")
def cancel_purchase_order(po_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    business_date = lock_business_date(db)
    po = db.execute(select(purchase_orders).where(purchase_orders.c.id == po_id).with_for_update()).mappings().first()
    if po is None: raise HTTPException(status_code=404, detail="Purchase order not found")
    if po["business_date"] != business_date: raise HTTPException(status_code=409, detail="Purchase order belongs to a different business date")
    if po["status"] not in {"draft", "approved"}: raise HTTPException(status_code=409, detail="Only unreceived purchase orders can be cancelled")
    if db.scalar(select(purchase_order_lines.c.id).where(purchase_order_lines.c.purchase_order_id == po_id, purchase_order_lines.c.received_quantity > 0).limit(1)) is not None: raise HTTPException(status_code=409, detail="Purchase order with receipts cannot be cancelled")
    db.execute(update(purchase_orders).where(purchase_orders.c.id == po_id).values(status="cancelled", updated_at=datetime.utcnow()))
    audit(db, user.id, "purchase_order_cancelled", "purchase_order", po_id, {"business_date": str(business_date)})
    db.commit()
    return build_po(db, dict(db.execute(select(purchase_orders).where(purchase_orders.c.id == po_id)).mappings().one()))


@router.post("/orders/{po_id}/receive", status_code=201)
def receive_purchase_order(po_id: int, payload: ReceiveRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    key = (idempotency_key or "").strip()
    if not key: raise HTTPException(status_code=400, detail="Idempotency-Key header is required for purchase receipt")
    business_date = lock_business_date(db)
    po = db.execute(select(purchase_orders).where(purchase_orders.c.id == po_id).with_for_update()).mappings().first()
    if po is None: raise HTTPException(status_code=404, detail="Purchase order not found")
    expected = receipt_fingerprint(po_id, business_date, payload.lines, payload.notes)
    existing = db.execute(select(goods_receipts).where(goods_receipts.c.idempotency_key == key).with_for_update()).mappings().first()
    if existing is not None:
        if existing["idempotency_fingerprint"] != expected: raise HTTPException(status_code=409, detail="Idempotency key is already bound to a different purchase receipt")
        return {"id": existing["id"], "grn_no": existing["grn_no"], "purchase_order_id": existing["purchase_order_id"], "business_date": existing["business_date"], "status": "posted", "replayed": True}
    if po["business_date"] != business_date: raise HTTPException(status_code=409, detail="Purchase order belongs to a different business date")
    if po["status"] not in {"approved", "partially_received"}: raise HTTPException(status_code=409, detail="Only approved or partially received purchase orders can be received")
    line_ids = [x.purchase_order_line_id for x in payload.lines]
    if len(line_ids) != len(set(line_ids)): raise HTTPException(status_code=400, detail="Each purchase order line may appear only once per receipt")
    po_lines = {r["id"]: dict(r) for r in db.execute(select(purchase_order_lines).where(purchase_order_lines.c.id.in_(line_ids), purchase_order_lines.c.purchase_order_id == po_id).with_for_update()).mappings().all()}
    if len(po_lines) != len(line_ids): raise HTTPException(status_code=404, detail="One or more purchase order lines were not found")
    stock_ids = [r["stock_item_id"] for r in po_lines.values()]
    stocks = {s.id: s for s in db.scalars(select(StockItem).where(StockItem.id.in_(stock_ids)).with_for_update()).all()}
    if len(stocks) != len(stock_ids): raise HTTPException(status_code=404, detail="One or more stock items were not found")
    for req in payload.lines:
        line = po_lines[req.purchase_order_line_id]
        if qty(line["received_quantity"] + req.quantity) > qty(line["ordered_quantity"]): raise HTTPException(status_code=409, detail=f"Receipt would exceed ordered quantity for PO line {line['id']}")
        if not stocks[line["stock_item_id"]].active: raise HTTPException(status_code=409, detail=f"Stock item {line['stock_item_id']} is inactive")
    receipt = db.execute(insert(goods_receipts).values(grn_no=f"GRN-{business_date.strftime('%Y%m%d')}-{token_hex(4).upper()}", purchase_order_id=po_id, supplier_id=po["supplier_id"], business_date=business_date, idempotency_key=key, idempotency_fingerprint=expected, created_by=user.id, received_at=datetime.utcnow(), notes=payload.notes).returning(goods_receipts)).mappings().one()
    for req in payload.lines:
        line = po_lines[req.purchase_order_line_id]; incoming = qty(req.quantity); unit_cost = money(req.unit_cost if req.unit_cost is not None else line["unit_cost"]); stock = stocks[line["stock_item_id"]]
        stock.on_hand = qty(stock.on_hand + incoming)
        db.execute(update(purchase_order_lines).where(purchase_order_lines.c.id == line["id"]).values(received_quantity=qty(line["received_quantity"] + incoming)))
        db.execute(insert(goods_receipt_lines).values(receipt_id=receipt["id"], purchase_order_line_id=line["id"], quantity=incoming, unit_cost=unit_cost))
        db.add(StockMovement(stock_item_id=stock.id, business_date=business_date, quantity=incoming, movement_type="purchase_receipt", reference_type="goods_receipt", reference_id=str(receipt["id"]), unit_cost=unit_cost, created_by=user.id))
    refreshed = db.execute(select(purchase_order_lines).where(purchase_order_lines.c.purchase_order_id == po_id)).mappings().all()
    new_status = "received" if all(qty(r["received_quantity"]) == qty(r["ordered_quantity"]) for r in refreshed) else "partially_received"
    db.execute(update(purchase_orders).where(purchase_orders.c.id == po_id).values(status=new_status, updated_at=datetime.utcnow()))
    audit(db, user.id, "purchase_receipt_posted", "goods_receipt", receipt["id"], {"purchase_order_id": po_id, "line_count": len(payload.lines), "business_date": str(business_date)})
    db.commit()
    return {"id": receipt["id"], "grn_no": receipt["grn_no"], "purchase_order_id": po_id, "business_date": business_date, "status": "posted", "replayed": False}


@router.get("/receipts")
def list_receipts(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    return [dict(r) for r in db.execute(select(goods_receipts).order_by(goods_receipts.c.id.desc()).limit(500)).mappings().all()]
