import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    BusinessDateState,
    Folio,
    FinancialTransaction,
    Guest,
    MenuItem,
    Payment,
    Reservation,
    RestaurantOrder,
    RestaurantOrderItem,
    Role,
    StockItem,
    StockMovement,
    User,
)
from app.restaurant_pos import PaymentCreate, add_pos_payment, cancel_order, post_order, void_posted_order


class RestaurantPosIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.today = date(2026, 9, 9)
        self.db.add(BusinessDateState(id=1, current_business_date=self.today))
        role = Role(name="admin")
        self.db.add(role)
        self.db.flush()
        self.user = User(username="admin", password_hash="test", role_id=role.id)
        guest = Guest(full_name="POS Guest")
        self.db.add_all([self.user, guest])
        self.db.flush()
        reservation = Reservation(guest_id=guest.id, check_in=self.today, check_out=date(2026, 9, 10), status="checked_in")
        self.db.add(reservation)
        self.db.flush()
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.flush()
        self.reservation_id = reservation.id
        self.folio_id = folio.id
        self.user_id = self.user.id
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()
        self.engine.dispose()

    def _make_order(self, *, order_no: str, price: Decimal, quantity: Decimal = Decimal("1"), stock: StockItem | None = None):
        menu = MenuItem(
            name=f"Menu {order_no}",
            category="food",
            unit_price=price,
            stock_item_id=stock.id if stock else None,
            stock_quantity_per_unit=Decimal("0.100") if stock else Decimal("0"),
        )
        self.db.add(menu)
        self.db.flush()
        order = RestaurantOrder(order_no=order_no, folio_id=self.folio_id, reservation_id=self.reservation_id, business_date=self.today, created_by=self.user_id)
        self.db.add(order)
        self.db.flush()
        self.db.add(RestaurantOrderItem(order_id=order.id, menu_item_id=menu.id, description=menu.name, quantity=quantity, unit_price=menu.unit_price, stock_quantity_per_unit=menu.stock_quantity_per_unit))
        self.db.commit()
        return order, menu

    def test_post_is_atomic_when_stock_is_insufficient(self):
        stock = StockItem(sku="CHICKEN", name="Chicken", unit="kg", on_hand=Decimal("1.000"))
        self.db.add(stock)
        self.db.flush()
        order, _ = self._make_order(order_no="POS-TEST-1", price=Decimal("1000.00"), quantity=Decimal("20"), stock=stock)
        with self.assertRaises(HTTPException) as ctx:
            post_order(order.id, self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()
        self.assertEqual(self.db.get(RestaurantOrder, order.id).status, "open")
        self.assertEqual(self.db.get(StockItem, stock.id).on_hand, Decimal("1.000"))
        self.assertIsNone(self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.folio_id == self.folio_id)))

    def test_posting_creates_folio_charges_service_charge_and_stock_movement(self):
        stock = StockItem(sku="COFFEE", name="Coffee", unit="kg", on_hand=Decimal("5.000"))
        self.db.add(stock)
        self.db.flush()
        order, _ = self._make_order(order_no="POS-TEST-2", price=Decimal("500.00"), quantity=Decimal("2"), stock=stock)
        result = post_order(order.id, self.db, self.user)
        self.assertEqual(result["status"], "posted")
        self.assertEqual(result["subtotal"], Decimal("1000.00"))
        self.assertEqual(result["service_charge"], Decimal("100.00"))
        self.assertEqual(result["total"], Decimal("1100.00"))
        self.assertEqual(self.db.get(StockItem, stock.id).on_hand, Decimal("4.800"))
        self.assertEqual(self.db.scalar(select(func.count(FinancialTransaction.id)).where(FinancialTransaction.folio_id == self.folio_id)), 2)
        movements = self.db.scalars(select(StockMovement).where(StockMovement.reference_id == str(order.id))).all()
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].quantity, Decimal("-0.200"))

    def test_business_date_mismatch_blocks_posting_without_mutation(self):
        order, _ = self._make_order(order_no="POS-TEST-3", price=Decimal("100.00"))
        order.business_date = date(2026, 9, 8)
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            post_order(order.id, self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()
        self.assertEqual(self.db.get(RestaurantOrder, order.id).status, "open")
        self.assertEqual(self.db.scalar(select(func.count(FinancialTransaction.id)).where(FinancialTransaction.folio_id == self.folio_id)), 0)

    def test_cancel_unposted_order_has_no_financial_effect(self):
        order, _ = self._make_order(order_no="POS-TEST-4", price=Decimal("200.00"))
        result = cancel_order(order.id, "customer cancelled", self.db, self.user)
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.folio_id == self.folio_id)))

    def test_pos_payment_is_idempotent(self):
        order, _ = self._make_order(order_no="POS-TEST-5", price=Decimal("100.00"))
        post_order(order.id, self.db, self.user)
        payload = PaymentCreate(amount=Decimal("1100.00"), method="cash", reference="r1")
        first = add_pos_payment(order.id, payload, "pos-key-1", self.db, self.user)
        second = add_pos_payment(order.id, payload, "pos-key-1", self.db, self.user)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.db.scalar(select(func.count(Payment.id)).where(Payment.folio_id == self.folio_id)), 1)

    def test_void_posted_order_reverses_finance_and_restores_stock(self):
        stock = StockItem(sku="TEA", name="Tea", unit="kg", on_hand=Decimal("2.000"))
        self.db.add(stock)
        self.db.flush()
        order, _ = self._make_order(order_no="POS-TEST-6", price=Decimal("200.00"), quantity=Decimal("1"), stock=stock)
        post_order(order.id, self.db, self.user)
        before = self.db.get(StockItem, stock.id).on_hand
        result = void_posted_order(order.id, "manager correction", self.db, self.user)
        self.assertEqual(result["status"], "voided")
        self.assertEqual(self.db.get(StockItem, stock.id).on_hand, before + Decimal("0.100"))
        reversed_count = self.db.scalar(select(func.count(FinancialTransaction.id)).where(FinancialTransaction.folio_id == self.folio_id, FinancialTransaction.status == "reversed"))
        self.assertGreaterEqual(reversed_count, 2)
        self.assertEqual(self.db.scalar(select(func.count(StockMovement.id)).where(StockMovement.reference_id == str(order.id))), 2)


if __name__ == "__main__":
    unittest.main()
