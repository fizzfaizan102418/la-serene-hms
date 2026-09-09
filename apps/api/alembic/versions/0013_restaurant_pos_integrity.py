"""Add transactional Restaurant/POS and stock domain."""
from alembic import op
import sqlalchemy as sa

revision = "0013_restaurant_pos_integrity"
down_revision = "0012_night_audit_closure_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False, server_default="unit"),
        sa.Column("on_hand", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("sku"),
        sa.UniqueConstraint("name"),
        sa.CheckConstraint("on_hand >= 0", name="ck_stock_items_nonnegative_on_hand"),
    )
    op.create_index("ix_stock_items_sku", "stock_items", ["sku"])
    op.create_index("ix_stock_items_name", "stock_items", ["name"])
    op.create_index("ix_stock_items_active", "stock_items", ["active"])

    op.create_table(
        "menu_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False, server_default="food"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("stock_item_id", sa.Integer(), sa.ForeignKey("stock_items.id"), nullable=True),
        sa.Column("stock_quantity_per_unit", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name"),
        sa.CheckConstraint("unit_price >= 0", name="ck_menu_items_nonnegative_price"),
        sa.CheckConstraint("stock_quantity_per_unit >= 0", name="ck_menu_items_nonnegative_stock_recipe"),
    )
    op.create_index("ix_menu_items_name", "menu_items", ["name"])
    op.create_index("ix_menu_items_active", "menu_items", ["active"])
    op.create_index("ix_menu_items_stock_item_id", "menu_items", ["stock_item_id"])

    op.create_table(
        "restaurant_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_no", sa.String(length=40), nullable=False),
        sa.Column("folio_id", sa.Integer(), sa.ForeignKey("folios.id"), nullable=False),
        sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("service_charge_rate", sa.Numeric(5, 4), nullable=False, server_default="0.10"),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("void_reason", sa.String(length=300), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("order_no"),
        sa.CheckConstraint("status IN ('open','posted','cancelled','voided')", name="ck_restaurant_orders_status"),
        sa.CheckConstraint("service_charge_rate >= 0 AND service_charge_rate <= 1", name="ck_restaurant_orders_service_charge_rate"),
    )
    op.create_index("ix_restaurant_orders_order_no", "restaurant_orders", ["order_no"])
    op.create_index("ix_restaurant_orders_folio_id", "restaurant_orders", ["folio_id"])
    op.create_index("ix_restaurant_orders_reservation_id", "restaurant_orders", ["reservation_id"])
    op.create_index("ix_restaurant_orders_business_date", "restaurant_orders", ["business_date"])
    op.create_index("ix_restaurant_orders_status", "restaurant_orders", ["status"])

    op.create_table(
        "restaurant_order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("restaurant_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("menu_item_id", sa.Integer(), sa.ForeignKey("menu_items.id"), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("stock_quantity_per_unit", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("folio_item_id", sa.Integer(), sa.ForeignKey("folio_items.id"), nullable=True),
        sa.Column("reversed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("folio_item_id"),
        sa.CheckConstraint("quantity > 0", name="ck_restaurant_order_items_positive_quantity"),
        sa.CheckConstraint("unit_price >= 0", name="ck_restaurant_order_items_nonnegative_price"),
        sa.CheckConstraint("stock_quantity_per_unit >= 0", name="ck_restaurant_order_items_nonnegative_stock_recipe"),
    )
    op.create_index("ix_restaurant_order_items_order_id", "restaurant_order_items", ["order_id"])
    op.create_index("ix_restaurant_order_items_menu_item_id", "restaurant_order_items", ["menu_item_id"])

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_item_id", sa.Integer(), sa.ForeignKey("stock_items.id"), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("movement_type", sa.String(length=30), nullable=False),
        sa.Column("reference_type", sa.String(length=40), nullable=False),
        sa.Column("reference_id", sa.String(length=50), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("quantity <> 0", name="ck_stock_movements_nonzero_quantity"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_stock_movements_nonnegative_cost"),
    )
    op.create_index("ix_stock_movements_stock_item_id", "stock_movements", ["stock_item_id"])
    op.create_index("ix_stock_movements_business_date", "stock_movements", ["business_date"])
    op.create_index("ix_stock_movements_movement_type", "stock_movements", ["movement_type"])

    bind = op.get_bind()
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
            CREATE TRIGGER trg_stock_movement_business_date_guard
            BEFORE INSERT ON stock_movements FOR EACH ROW
            EXECUTE FUNCTION hms_guard_stock_movement_business_date();
        """))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_stock_movement_immutable()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Stock movements are immutable'; END; $$;
        """))
        op.execute(sa.text("""
            CREATE TRIGGER trg_stock_movement_immutable_update
            BEFORE UPDATE ON stock_movements FOR EACH ROW EXECUTE FUNCTION hms_guard_stock_movement_immutable();
        """))
        op.execute(sa.text("""
            CREATE TRIGGER trg_stock_movement_immutable_delete
            BEFORE DELETE ON stock_movements FOR EACH ROW EXECUTE FUNCTION hms_guard_stock_movement_immutable();
        """))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_stock_movement_immutable_delete ON stock_movements"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_stock_movement_immutable_update ON stock_movements"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_stock_movement_immutable()"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_stock_movement_business_date_guard ON stock_movements"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_stock_movement_business_date()"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_restaurant_order_status_guard ON restaurant_orders"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_restaurant_order_status()"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_restaurant_order_business_date_guard ON restaurant_orders"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_restaurant_order_business_date()"))
    op.drop_table("stock_movements")
    op.drop_table("restaurant_order_items")
    op.drop_table("restaurant_orders")
    op.drop_table("menu_items")
    op.drop_table("stock_items")
