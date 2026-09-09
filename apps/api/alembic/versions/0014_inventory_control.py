"""Add inventory operation integrity guards."""
from alembic import op
import sqlalchemy as sa

revision = "0014_inventory_control"
down_revision = "0013_restaurant_pos_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.db import Base
    import app.models  # noqa: F401
    import app.pms_core  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    if bind.dialect.name == "postgresql":
        op.execute(sa.text(
            "ALTER TABLE stock_items DROP CONSTRAINT IF EXISTS ck_stock_items_nonnegative_on_hand"
        ))
        op.execute(sa.text(
            "ALTER TABLE stock_items ADD CONSTRAINT ck_stock_items_nonnegative_on_hand CHECK (on_hand >= 0)"
        ))
        op.execute(sa.text(
            "ALTER TABLE stock_operations DROP CONSTRAINT IF EXISTS ck_stock_operations_nonzero_quantity"
        ))
        op.execute(sa.text(
            "ALTER TABLE stock_operations ADD CONSTRAINT ck_stock_operations_nonzero_quantity CHECK (quantity <> 0)"
        ))

        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_stock_operation_business_date()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE current_date_value date;
            BEGIN
                SELECT current_business_date INTO current_date_value
                FROM business_date_state WHERE id = 1 FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Business date is not initialized';
                END IF;
                IF NEW.business_date <> current_date_value THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Stock operation date %s is not the current business date %s', NEW.business_date, current_date_value);
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("""
            DROP TRIGGER IF EXISTS trg_stock_operation_business_date_guard ON stock_operations;
            CREATE TRIGGER trg_stock_operation_business_date_guard
            BEFORE INSERT ON stock_operations FOR EACH ROW
            EXECUTE FUNCTION hms_guard_stock_operation_business_date();
        """))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_stock_operation_immutable()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Stock operations are immutable';
            END; $$;
        """))
        op.execute(sa.text("""
            DROP TRIGGER IF EXISTS trg_stock_operation_immutable_update ON stock_operations;
            CREATE TRIGGER trg_stock_operation_immutable_update
            BEFORE UPDATE ON stock_operations FOR EACH ROW EXECUTE FUNCTION hms_guard_stock_operation_immutable();
        """))
        op.execute(sa.text("""
            DROP TRIGGER IF EXISTS trg_stock_operation_immutable_delete ON stock_operations;
            CREATE TRIGGER trg_stock_operation_immutable_delete
            BEFORE DELETE ON stock_operations FOR EACH ROW EXECUTE FUNCTION hms_guard_stock_operation_immutable();
        """))


def downgrade() -> None:
    raise RuntimeError("Inventory history is append-only; restore a verified backup instead of destructively rolling back stock controls.")
