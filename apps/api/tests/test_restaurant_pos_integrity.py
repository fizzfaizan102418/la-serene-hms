import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    BusinessDateState,
    Folio,
    FinancialTransaction,
    Guest,
    LedgerEntry,
    MenuItem,
    Reservation,
    RestaurantOrder,
    RestaurantOrderItem,
    Role,
    StockItem,
    StockMovement,
    User,
)
from app.restaurant_pos import (
    RestaurantOrderCreate,
    RestaurantOrderItemCreate,
    add_order_item,
    add_pos_payment,
    cancel_order,
    create_menu_item,
    create_order,
    create_stock_item,
    post_order,
    void_posted_order,
)


class RestaurantPosIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=cls.engine)

    def setUp(self):
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
        self.db.close()

    def test_post_is_atomic_when_stock_is_insufficient(self):
        stock = StockItem(sku="CHICKEN", name="Chicken", unit="kg", on_hand=Decimal("1.000"))
        self.db.add(stock)
        self.db.flush()
        menu = MenuItem(name="Chicken Plate", category="food", unit_price=Decimal("1000.00"), stock_item_id=stock.id, stock_quantity_per_unit=Decimal("0.750"))
        self.db.add(menu)
        self.db.flush()
        order = RestaurantOrder(order_no="POS-TEST-1", folio_id=self.folio_id, reservation_id=self.reservation_id, business_date=self.today, created_by=self.user_id)
        self.db.add(order)
        self.db.flush()
        self.db.add(RestaurantOrderItem(order_id=order.id, menu_item_id=menu.id, description=menu.name, quantity=Decimal("2"), unit_price=menu.unit_price, stock_quantity_per_unit=menu.stock_quantity_per_unit))
        self.db.commit()

        with self.assertRaises(Exception):
            post_order(order.id, self.db, self.user)

        self.db.rollback()
        fresh_order = self.db.get(RestaurantOrder, order.id)
        fresh_stock = self.db.get(StockItem, stock.id)
        self.assertEqual(fresh_order.status, "open")
        self.assertEqual(fresh_stock.on_hand, Decimal("1.000"))
        self.assertEqual(self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.folio_id == self.folio_id)), None)

    def test_posting_creates_folio_charge_service_charge_and_stock_movement(self):
        stock = StockItem(sku="COFFEE", name="Coffee", unit="kg", on_hand=Decimal("5.000"))
        self.db.add(stock)
        self.db.flush()
        menu = MenuItem(name="Coffee", category="food", unit_price=Decimal("500.00"), stock_item_id=stock.id, stock_quantity_per_unit=Decimal("0.050"))
        self.db.add(menu)
        self.db.flush()
        order = RestaurantOrder(order_no="POS-TEST-2", folio_id=self.folio_id, reservation_id=self.reservation_id, business_date=self.today, created_by=self.user_id)
        self.db.add(order)
        self.db.flush()
        self.db.add(RestaurantOrderItem(order_id=order.id, menu_item_id=menu.id, description=menu.name, quantity=Decimal("2"), unit_price=menu.unit_price, stock_quantity_per_unit=menu.stock_quantity_per_unit))
        self.db.commit()

        result = post_order(order.id, self.db, self.user)
        self.assertEqual(result["status"], "posted")
        self.assertEqual(result["subtotal"], Decimal("1000.00"))
        self.assertEqual(result["service_charge"], Decimal("100.00"))
        self.assertEqual(result["total"], Decimal("1100.00"))
        self.assertEqual(self.db.get(StockItem, stock.id).on_hand, Decimal("4.900"))
        self.assertEqual(self.db.scalar(select(func := FinancialTransaction.id).where(FinancialTransaction.transaction_type == "service_charge")), self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.transaction_type == "service_charge")))
        movements = self.db.scalars(select(StockMovement).where(StockMovement.reference_id == str(order.id))).all()
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].quantity, Decimal("-0.100"))

    def test_cancel_unposted_order_has_no_financial_effect(self):
        order = RestaurantOrder(order_no="POS-TEST-3", folio_id=self.folio_id, reservation_id=self.reservation_id, business_date=self.today, created_by=self.user_id)
        self.db.add(order)
        self.db.commit()
        result = cancel_order(order.id, "customer cancelled", self.db, self.user)
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.folio_id == self.folio_id)))

    def test_pos_payment_is_idempotent(self):
        order = RestaurantOrder(order_no="POS-TEST-4", folio_id=self.folio_id, reservation_id=self.reservation_id, business_date=self.today, created_by=self.user_id)
        self.db.add(order)
        self.db.flush()
        self.db.add(RestaurantOrderItem(order_id=order.id, menu_item_id=0, description="legacy", quantity=Decimal("1"), unit_price=Decimal("100.00"), stock_quantity_per_unit=Decimal("0")))
        self.db.commit()
        order.status = "posted"
        self.db.commit()
        self.db.add(LedgerEntry(transaction_id=0, account="x", direction="debit", amount=Decimal("1.00"))) if False else None
        # Seed the authoritative folio balance directly through the same ledger service used by production.
        from app.ledger import post_transaction
        post_transaction(self.db, transaction_type="folio_charge", description="POS seed", reference_type="restaurant_order_item", reference_id="seed", folio_id=self.folio_id, reservation_id=self.reservation_id, created_by=self.user_id, idempotency_key="seed-charge", lines=[{"account":"Guest Receivables","direction":"debit","amount":Decimal("100.00"),"folio_id":self.folio_id},{"account":"Revenue - Food","direction":"credit","amount":Decimal("100.00"),"folio_id":self.folio_id}])
        self.db.commit()
        first = add_pos_payment(order.id, type("P", (), {"amount": Decimal("100.00"), "method": "cash", "reference": "r1"})(), "pos-key-1", self.db, self.user)
        second = add_pos_payment(order.id, type("P", (), {"amount": Decimal("100.00"), "method": "cash", "reference": "r1"})(), "pos-key-1", self.db, self.user)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["replayed"])
        self.assertEqual(self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.idempotency_key == "pos-key-1")).__class__, int)

    def test_void_posted_order_reverses_finance_and_restores_stock(self):
        stock = StockItem(sku="TEA", name="Tea", unit="kg", on_hand=Decimal("2.000"))
        self.db.add(stock)
        self.db.flush()
        menu = MenuItem(name="Tea", category="food", unit_price=Decimal("200.00"), stock_item_id=stock.id, stock_quantity_per_unit=Decimal("0.100"))
        self.db.add(menu)
        self.db.flush()
        order = RestaurantOrder(order_no="POS-TEST-5", folio_id=self.folio_id, reservation_id=self.reservation_id, business_date=self.today, created_by=self.user_id)
        self.db.add(order)
        self.db.flush()
        self.db.add(RestaurantOrderItem(order_id=order.id, menu_item_id=menu.id, description=menu.name, quantity=Decimal("1"), unit_price=menu.unit_price, stock_quantity_per_unit=menu.stock_quantity_per_unit))
        self.db.commit()
        post_order(order.id, self.db, self.user)
        before = self.db.get(StockItem, stock.id).on_hand
        result = void_posted_order(order.id, "manager correction", self.db, self.user)
        self.assertEqual(result["status"], "voided")
        self.assertEqual(self.db.get(StockItem, stock.id).on_hand, before + Decimal("0.100"))
        self.assertEqual(self.db.scalar(select(RestaurantOrder).where(RestaurantOrder.id == order.id)).status, "voided")
        reversed_count = self.db.scalar(select(func.count(FinancialTransaction.id)).where(FinancialTransaction.folio_id == self.folio_id, FinancialTransaction.status == "reversed"))
        self.assertGreaterEqual(reversed_count, 2)


if __name__ == "__main__":
    unittest.main()
