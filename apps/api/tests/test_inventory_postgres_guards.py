import unittest
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import engine
from app.inventory import stock_operations
from app.models import BusinessDateState, Role, StockItem, StockMovement, User


class InventoryPostgreSQLGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if engine.dialect.name != "postgresql":
            raise unittest.SkipTest("HMS_DATABASE_URL is not PostgreSQL")

    def test_stock_operation_and_movement_tables_exist(self):
        names = set(inspect(engine).get_table_names())
        self.assertIn("stock_operations", names)
        self.assertIn("stock_movements", names)

    def _ensure_state_and_role(self, db):
        state = db.get(BusinessDateState, 1)
        if state is None:
            state = BusinessDateState(id=1, current_business_date=date(2026, 9, 9))
            db.add(state)
        role = db.scalar(select(Role).where(Role.name == "admin"))
        if role is None:
            role = Role(name="admin")
            db.add(role)
        db.flush()
        return state, role

    def test_business_date_guard_rejects_stale_inventory_operation(self):
        with Session(engine) as db:
            state, role = self._ensure_state_and_role(db)
            stock = StockItem(sku="CI-F-GUARD-" + str(id(self)), name="CI F Guard " + str(id(self)), unit="unit", on_hand=Decimal("1.000"))
            user = User(username="ci-f-guard-1-" + str(id(self)), password_hash="test", role_id=role.id)
            db.add_all([stock, user])
            db.flush()
            with self.assertRaises(DBAPIError):
                db.execute(stock_operations.insert().values(
                    operation_no="CI-F-GUARD-OP-" + str(stock.id),
                    idempotency_key="CI-F-GUARD-KEY-" + str(stock.id),
                    idempotency_fingerprint="f" * 64,
                    business_date=state.current_business_date - timedelta(days=1),
                    operation_type="receipt",
                    stock_item_id=stock.id,
                    quantity=Decimal("1.000"),
                    unit_cost=Decimal("1.00"),
                    reason="stale date test",
                    created_by=user.id,
                ))
            db.rollback()

    def test_stock_movement_is_immutable(self):
        with Session(engine) as db:
            state, role = self._ensure_state_and_role(db)
            stock = StockItem(sku="CI-F-GUARD-STOCK-" + str(id(self)), name="CI F Guard Stock " + str(id(self)), unit="unit", on_hand=Decimal("1.000"))
            user = User(username="ci-f-guard-user-" + str(id(self)), password_hash="test", role_id=role.id)
            db.add_all([stock, user])
            db.flush()
            movement = StockMovement(stock_item_id=stock.id, business_date=state.current_business_date, quantity=Decimal("1.000"), movement_type="opening", reference_type="ci", reference_id=str(stock.id), unit_cost=Decimal("1.00"), created_by=user.id)
            db.add(movement)
            db.commit()
            movement_id = movement.id
            movement.quantity = Decimal("2.000")
            with self.assertRaises(DBAPIError):
                db.flush()
            db.rollback()
            movement = db.get(StockMovement, movement_id)
            db.delete(movement)
            with self.assertRaises(DBAPIError):
                db.flush()
            db.rollback()
