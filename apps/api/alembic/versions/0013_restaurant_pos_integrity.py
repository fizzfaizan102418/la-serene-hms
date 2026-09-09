"""Add transactional Restaurant/POS and stock domain."""
from alembic import op
import sqlalchemy as sa

revision = "0013_restaurant_pos_integrity"
down_revision = "0012_night_audit_closure_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The HMS bootstrap revisions use SQLAlchemy metadata as the schema
    # authority. New ORM tables may therefore already exist on a fresh DB
    # after revision 0001. create_all is idempotent and also creates these
    # tables on already-migrated databases upgrading from 0012.
    from app.db import Base
    import app.models  # noqa: F401
    import app.pms_core  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    if bind.dialect.name == "postgresql":
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_restaurant_order_business_date()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE current_date_value date;
            BEGIN
                SELECT current_business_date INTO current_date_value
                FROM business_date_state WHERE id = 1 FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Business date is not initialized';
                END IF;
                IF NEW.business_date <> current_date_value THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Restaurant order date %s is not the current business date %s', NEW.business_date, current_date_value);
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("""
            DROP TRIGGER IF EXISTS trg_restaurant_order_business_date_guard ON restaurant_orders;
            CREATE TRIGGER trg_restaurant_order_business_date_guard
            BEFORE INSERT ON restaurant_orders FOR EACH ROW
            EXECUTE FUNCTION hms_guard_restaurant_order_business_date();
        """))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_restaurant_order_status()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.status = OLD.status THEN RETURN NEW; END IF;
                IF OLD.status='open' AND NEW.status IN ('posted','cancelled') THEN RETURN NEW; END IF;
                IF OLD.status='posted' AND NEW.status='voided' THEN RETURN NEW; END IF;
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Invalid restaurant order status transition %s -> %s', OLD.status, NEW.status);
            END; $$;
        """))
        op.execute(sa.text("""
            DROP TRIGGER IF EXISTS trg_restaurant_order_status_guard ON restaurant_orders;
            CREATE TRIGGER trg_restaurant_order_status_guard
            BEFORE UPDATE OF status ON restaurant_orders FOR EACH ROW
            EXECUTE FUNCTION hms_guard_restaurant_order_status();
        """))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_stock_movement_business_date()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE current_date_value date;
            BEGIN
                SELECT current_business_date INTO current_date_value
                FROM business_date_state WHERE id = 1 FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Business date is not initialized';
                END IF;
                IF NEW.business_date <> current_date_value THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Stock movement date %s is not the current business date %s', NEW.business_date, current_date_value);
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("""
            DROP TRIGGER IF EXISTS trg_stock_movement_business_date_guard ON stock_movements;
            CREATE TRIGGER trg_stock_movement_business_date_guard
            BEFORE INSERT ON stock_movements FOR EACH ROW
            EXECUTE FUNCTION hms_guard_stock_movement_business_date();
        """))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_stock_movement_immutable()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Stock movements are immutable';
            END; $$;
        """))
        op.execute(sa.text("""
            DROP TRIGGER IF EXISTS trg_stock_movement_immutable_update ON stock_movements;
            CREATE TRIGGER trg_stock_movement_immutable_update
            BEFORE UPDATE ON stock_movements FOR EACH ROW EXECUTE FUNCTION hms_guard_stock_movement_immutable();
        """))
        op.execute(sa.text("""
            DROP TRIGGER IF EXISTS trg_stock_movement_immutable_delete ON stock_movements;
            CREATE TRIGGER trg_stock_movement_immutable_delete
            BEFORE DELETE ON stock_movements FOR EACH ROW EXECUTE FUNCTION hms_guard_stock_movement_immutable();
        """))


def downgrade() -> None:
    # Financial/POS history is append-only. A production rollback should use
    # a verified database backup rather than destructively removing data.
    raise RuntimeError("Restaurant/POS history is append-only; restore a verified backup instead of dropping Phase E tables.")
