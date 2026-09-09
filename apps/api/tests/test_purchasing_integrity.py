import unittest
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import BusinessDateState, Role, StockItem, StockMovement, User
from app.purchasing import (
    POLineCreate,
    PurchaseOrderCreate,
    ReceiveLine,
    ReceiveRequest,
    SupplierCreate,
    approve_purchase_order,
    cancel_purchase_order,
    create_purchase_order,
    create_supplier,
    goods_receipts,
    purchase_order_lines,
    purchase_orders,
    receive_purchase_order,
)


class PurchasingIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.today = date(2026, 9, 9)
        self.db.add(BusinessDateState(id=1, current_business_date=self.today))
        role = Role(name="admin")
        self.db.add(role)
        self.db.flush()
        self.user = User(username="purchasing-admin", password_hash="test", role_id=role.id)
        self.db.add(self.user)
        self.stock = StockItem(sku="RICE", name="Rice", unit="kg", on_hand=Decimal("10.000"))
        self.db.add(self.stock)
        self.db.flush()
        self.db.add(StockMovement(stock_item_id=self.stock.id, business_date=self.today, quantity=Decimal("10.000"), movement_type="opening", reference_type="stock_item", reference_id=str(self.stock.id), unit_cost=Decimal("0.00"), created_by=self.user.id))
        self.db.commit()
        self.supplier = create_supplier(SupplierCreate(code="SUP-01", name="Golden Foods"), self.db, self.user)
        self.po = create_purchase_order(PurchaseOrderCreate(supplier_id=self.supplier["id"], lines=[POLineCreate(stock_item_id=self.stock.id, ordered_quantity=Decimal("10.000"), unit_cost=Decimal("50.00"), description="Rice")]), self.db, self.user)

    def tearDown(self):
        self.db.rollback(); self.db.close(); self.engine.dispose()

    def test_purchase_order_starts_draft_and_requires_approval(self):
        self.assertEqual(self.po["status"], "draft")
        with self.assertRaises(HTTPException) as ctx:
            receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=self.po["lines"][0]["id"], quantity=Decimal("2.000"))]), "pre-approval", self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("10.000"))

    def test_approval_then_partial_receipt_updates_stock_and_po(self):
        approved = approve_purchase_order(self.po["id"], self.db, self.user)
        self.assertEqual(approved["status"], "approved")
        line_id = approved["lines"][0]["id"]
        receipt = receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=line_id, quantity=Decimal("4.000"), unit_cost=Decimal("52.00"))]), "grn-1", self.db, self.user)
        self.assertEqual(receipt["status"], "posted")
        po_row = self.db.execute(select(purchase_orders).where(purchase_orders.c.id == self.po["id"])).mappings().one()
        line_row = self.db.execute(select(purchase_order_lines).where(purchase_order_lines.c.id == line_id)).mappings().one()
        self.assertEqual(po_row["status"], "partially_received")
        self.assertEqual(line_row["received_quantity"], Decimal("4.000"))
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("14.000"))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(goods_receipts)), 1)
        self.assertEqual(self.db.scalar(select(func.sum(StockMovement.quantity)).where(StockMovement.stock_item_id == self.stock.id)), Decimal("14.000"))

    def test_full_receipt_closes_po_after_remaining_quantity(self):
        approved = approve_purchase_order(self.po["id"], self.db, self.user)
        line_id = approved["lines"][0]["id"]
        receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=line_id, quantity=Decimal("4.000"))]), "grn-2a", self.db, self.user)
        final = receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=line_id, quantity=Decimal("6.000"))]), "grn-2b", self.db, self.user)
        self.assertEqual(final["replayed"], False)
        po_row = self.db.execute(select(purchase_orders).where(purchase_orders.c.id == self.po["id"])).mappings().one()
        self.assertEqual(po_row["status"], "received")
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("20.000"))

    def test_over_receipt_is_atomic(self):
        approved = approve_purchase_order(self.po["id"], self.db, self.user)
        line_id = approved["lines"][0]["id"]
        with self.assertRaises(HTTPException) as ctx:
            receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=line_id, quantity=Decimal("11.000"))]), "over-1", self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("10.000"))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(goods_receipts)), 0)
        line = self.db.execute(select(purchase_order_lines).where(purchase_order_lines.c.id == line_id)).mappings().one()
        self.assertEqual(line["received_quantity"], Decimal("0.000"))

    def test_receipt_idempotency_replays_without_double_receipt(self):
        approved = approve_purchase_order(self.po["id"], self.db, self.user)
        line_id = approved["lines"][0]["id"]
        first = receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=line_id, quantity=Decimal("3.000"))]), "same-grn", self.db, self.user)
        second = receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=line_id, quantity=Decimal("3.000"))]), "same-grn", self.db, self.user)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("13.000"))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(goods_receipts)), 1)

    def test_receipt_idempotency_key_cannot_be_reused_with_different_payload(self):
        approved = approve_purchase_order(self.po["id"], self.db, self.user)
        line_id = approved["lines"][0]["id"]
        receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=line_id, quantity=Decimal("3.000"))]), "collision", self.db, self.user)
        with self.assertRaises(HTTPException) as ctx:
            receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=line_id, quantity=Decimal("4.000"))]), "collision", self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("13.000"))

    def test_business_date_mismatch_rejects_receipt_without_mutation(self):
        approved = approve_purchase_order(self.po["id"], self.db, self.user)
        line_id = approved["lines"][0]["id"]
        self.db.get(BusinessDateState, 1).current_business_date = self.today + timedelta(days=1)
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            receive_purchase_order(self.po["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=line_id, quantity=Decimal("2.000"))]), "stale-date", self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("10.000"))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(goods_receipts)), 0)

    def test_cancel_allowed_before_receipt_and_rejected_after_receipt(self):
        cancelled = cancel_purchase_order(self.po["id"], self.db, self.user)
        self.assertEqual(cancelled["status"], "cancelled")
        po2 = create_purchase_order(PurchaseOrderCreate(supplier_id=self.supplier["id"], lines=[POLineCreate(stock_item_id=self.stock.id, ordered_quantity=Decimal("5.000"), unit_cost=Decimal("50.00"), description="Rice")]), self.db, self.user)
        approve_purchase_order(po2["id"], self.db, self.user)
        receive_purchase_order(po2["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=po2["lines"][0]["id"], quantity=Decimal("1.000"))]), "grn-cancel", self.db, self.user)
        with self.assertRaises(HTTPException) as ctx:
            cancel_purchase_order(po2["id"], self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_receipt_requires_idempotency_key(self):
        approved = approve_purchase_order(self.po["id"], self.db, self.user)
        with self.assertRaises(HTTPException) as ctx:
            receive_purchase_order(approved["id"], ReceiveRequest(lines=[ReceiveLine(purchase_order_line_id=approved["lines"][0]["id"], quantity=Decimal("1.000"))]), None, self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("10.000"))


if __name__ == "__main__":
    unittest.main()
