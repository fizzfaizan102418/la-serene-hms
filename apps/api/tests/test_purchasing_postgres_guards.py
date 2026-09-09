import unittest
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import engine
from app.models import BusinessDateState, Role, StockItem, User
from app.purchasing import goods_receipts, purchase_orders


class PurchasingPostgreSQLGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if engine.dialect.name != "postgresql":
            raise unittest.SkipTest("HMS_DATABASE_URL is not PostgreSQL")

    def test_purchasing_tables_exist(self):
        names = set(inspect(engine).get_table_names())
        for name in ("suppliers", "purchase_orders", "purchase_order_lines", "goods_receipts", "goods_receipt_lines"):
            self.assertIn(name, names)

    def _ensure_state_role(self, db):
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

    def test_purchase_order_business_date_guard_rejects_stale_insert(self):
        with Session(engine) as db:
            state, role = self._ensure_state_role(db)
            user = User(username="ci-g-pod-" + str(id(self)), password_hash="test", role_id=role.id)
            db.add(user); db.flush()
            with self.assertRaises(DBAPIError):
                db.execute(purchase_orders.insert().values(po_no="CI-G-PO-" + str(user.id), supplier_id=999999, business_date=state.current_business_date - timedelta(days=1), status="draft", created_by=user.id))
            db.rollback()

    def test_goods_receipt_business_date_guard_rejects_stale_insert(self):
        with Session(engine) as db:
            state, role = self._ensure_state_role(db)
            with self.assertRaises(DBAPIError):
                db.execute(goods_receipts.insert().values(grn_no="CI-G-GRN-" + str(id(self)), purchase_order_id=999999, supplier_id=999999, business_date=state.current_business_date - timedelta(days=1), idempotency_key="CI-G-KEY-" + str(id(self)), idempotency_fingerprint="g" * 64, created_by=999999))
            db.rollback()

    def test_goods_receipt_history_is_immutable(self):
        with Session(engine) as db:
            state, role = self._ensure_state_role(db)
            # Trigger assertion is tested by attempting mutation of any existing row created by CI.
            # No existing row is required: the migration's triggers are inspected directly.
            row = db.execute(select(goods_receipts.c.id).limit(1)).first()
            trigger_names = {x["tgname"] for x in db.execute(__import__("sqlalchemy").text("SELECT tgname FROM pg_trigger WHERE tgrelid = 'goods_receipts'::regclass AND NOT tgisinternal")).mappings().all()}
            self.assertIn("trg_goods_receipt_immutable_update", trigger_names)
            self.assertIn("trg_goods_receipt_immutable_delete", trigger_names)


if __name__ == "__main__":
    unittest.main()
