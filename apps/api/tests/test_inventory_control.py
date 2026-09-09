import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.inventory import AdjustmentRequest, ReceiveRequest, adjust_stock, receive_stock, reconcile_stock_item, stock_operations
from app.models import BusinessDateState, Role, StockItem, StockMovement, User


class InventoryControlTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.today = date(2026, 9, 9)
        self.db.add(BusinessDateState(id=1, current_business_date=self.today))
        role = Role(name="admin")
        self.db.add(role)
        self.db.flush()
        self.user = User(username="inventory-admin", password_hash="test", role_id=role.id)
        self.db.add(self.user)
        self.stock = StockItem(sku="RICE", name="Rice", unit="kg", on_hand=Decimal("10.000"))
        self.db.add(self.stock)
        self.db.flush()
        self.db.add(StockMovement(stock_item_id=self.stock.id, business_date=self.today, quantity=Decimal("10.000"), movement_type="opening", reference_type="stock_item", reference_id=str(self.stock.id), unit_cost=Decimal("0.00"), created_by=self.user.id))
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()
        self.engine.dispose()

    def test_receive_is_atomic_and_updates_movement_ledger(self):
        result = receive_stock(ReceiveRequest(stock_item_id=self.stock.id, quantity=Decimal("5.500"), unit_cost=Decimal("100.00"), reason="Supplier delivery"), "recv-1", self.db, self.user)
        self.assertEqual(result["operation_type"], "receipt")
        self.assertEqual(result["quantity"], Decimal("5.500"))
        self.assertEqual(result["on_hand_after"], Decimal("15.500"))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(stock_operations)), 1)
        self.assertEqual(self.db.scalar(select(func.sum(StockMovement.quantity)).where(StockMovement.stock_item_id == self.stock.id)), Decimal("15.500"))

    def test_adjustment_allows_positive_and_negative_changes(self):
        plus = adjust_stock(AdjustmentRequest(stock_item_id=self.stock.id, quantity=Decimal("2.250"), reason="Count correction"), "adj-1", self.db, self.user)
        self.assertEqual(plus["on_hand_after"], Decimal("12.250"))
        minus = adjust_stock(AdjustmentRequest(stock_item_id=self.stock.id, quantity=Decimal("-1.250"), reason="Damaged goods"), "adj-2", self.db, self.user)
        self.assertEqual(minus["on_hand_after"], Decimal("11.000"))

    def test_adjustment_cannot_make_stock_negative(self):
        with self.assertRaises(HTTPException) as ctx:
            adjust_stock(AdjustmentRequest(stock_item_id=self.stock.id, quantity=Decimal("-11.000"), reason="Invalid count"), "adj-negative", self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("10.000"))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(stock_operations)), 0)

    def test_same_idempotency_key_replays_without_double_posting(self):
        first = receive_stock(ReceiveRequest(stock_item_id=self.stock.id, quantity=Decimal("3.000"), unit_cost=Decimal("55.00"), reason="Delivery A"), "same-key", self.db, self.user)
        second = receive_stock(ReceiveRequest(stock_item_id=self.stock.id, quantity=Decimal("3.000"), unit_cost=Decimal("55.00"), reason="Delivery A"), "same-key", self.db, self.user)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("13.000"))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(stock_operations)), 1)

    def test_reusing_idempotency_key_for_different_operation_is_rejected(self):
        receive_stock(ReceiveRequest(stock_item_id=self.stock.id, quantity=Decimal("1.000"), unit_cost=Decimal("50.00"), reason="Delivery B"), "collision-key", self.db, self.user)
        with self.assertRaises(HTTPException) as ctx:
            receive_stock(ReceiveRequest(stock_item_id=self.stock.id, quantity=Decimal("2.000"), unit_cost=Decimal("50.00"), reason="Delivery B"), "collision-key", self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.db.get(StockItem, self.stock.id).on_hand, Decimal("11.000"))

    def test_reconciliation_matches_immutable_movement_sum(self):
        receive_stock(ReceiveRequest(stock_item_id=self.stock.id, quantity=Decimal("4.000"), unit_cost=Decimal("40.00"), reason="Delivery C"), "reconcile-key", self.db, self.user)
        result = reconcile_stock_item(self.stock.id, self.db, self.user)
        self.assertEqual(result.reconciled, True)
        self.assertEqual(result.variance, Decimal("0.000"))
        self.assertEqual(result.movement_total, Decimal("14.000"))

    def test_missing_business_date_is_rejected(self):
        self.db.delete(self.db.get(BusinessDateState, 1))
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            receive_stock(ReceiveRequest(stock_item_id=self.stock.id, quantity=Decimal("1.000"), reason="No business date"), "no-date", self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
