"""Add supplier, purchase order, and goods receipt controls."""
from alembic import op
import sqlalchemy as sa

revision = "0015_purchasing_integrity"
down_revision = "0014_inventory_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False), sa.Column("contact_name", sa.String(120)),
        sa.Column("phone", sa.String(40)), sa.Column("email", sa.String(160)), sa.Column("address", sa.String(400)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("code", name="uq_suppliers_code"),
    )
    op.create_index("ix_suppliers_code", "suppliers", ["code"], unique=True)

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("po_no", sa.String(40), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("notes", sa.String(500)), sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("approved_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("po_no", name="uq_purchase_orders_po_no"),
        sa.CheckConstraint("status IN ('draft','approved','partially_received','received','cancelled')", name="ck_purchase_orders_status"),
    )
    op.create_index("ix_purchase_orders_po_no", "purchase_orders", ["po_no"], unique=True)
    op.create_index("ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"])
    op.create_index("ix_purchase_orders_business_date", "purchase_orders", ["business_date"])
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])

    op.create_table(
        "purchase_order_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stock_item_id", sa.Integer(), sa.ForeignKey("stock_items.id"), nullable=False),
        sa.Column("description", sa.String(200), nullable=False), sa.Column("ordered_quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("received_quantity", sa.Numeric(14, 3), nullable=False, server_default="0"), sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint("ordered_quantity > 0", name="ck_po_lines_ordered_positive"),
        sa.CheckConstraint("received_quantity >= 0 AND received_quantity <= ordered_quantity", name="ck_po_lines_received_valid"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_po_lines_unit_cost_nonnegative"),
    )
    op.create_index("ix_purchase_order_lines_purchase_order_id", "purchase_order_lines", ["purchase_order_id"])
    op.create_index("ix_purchase_order_lines_stock_item_id", "purchase_order_lines", ["stock_item_id"])

    op.create_table(
        "goods_receipts",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("grn_no", sa.String(40), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False), sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False), sa.Column("idempotency_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("notes", sa.String(500)),
        sa.UniqueConstraint("grn_no", name="uq_goods_receipts_grn_no"), sa.UniqueConstraint("idempotency_key", name="uq_goods_receipts_idempotency_key"),
    )
    op.create_index("ix_goods_receipts_grn_no", "goods_receipts", ["grn_no"], unique=True)
    op.create_index("ix_goods_receipts_purchase_order_id", "goods_receipts", ["purchase_order_id"])
    op.create_index("ix_goods_receipts_supplier_id", "goods_receipts", ["supplier_id"])
    op.create_index("ix_goods_receipts_business_date", "goods_receipts", ["business_date"])
    op.create_index("ix_goods_receipts_idempotency_key", "goods_receipts", ["idempotency_key"], unique=True)

    op.create_table(
        "goods_receipt_lines",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("receipt_id", sa.Integer(), sa.ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purchase_order_line_id", sa.Integer(), sa.ForeignKey("purchase_order_lines.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False), sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_goods_receipt_lines_positive"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_goods_receipt_lines_unit_cost_nonnegative"),
    )
    op.create_index("ix_goods_receipt_lines_receipt_id", "goods_receipt_lines", ["receipt_id"])
    op.create_index("ix_goods_receipt_lines_po_line_id", "goods_receipt_lines", ["purchase_order_line_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_purchase_order_business_date()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE current_date_value date;
            BEGIN
                SELECT current_business_date INTO current_date_value FROM business_date_state WHERE id = 1 FOR UPDATE;
                IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Business date is not initialized'; END IF;
                IF NEW.business_date <> current_date_value THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Purchase order date %s is not the current business date %s', NEW.business_date, current_date_value);
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("CREATE TRIGGER trg_purchase_order_business_date_guard BEFORE INSERT ON purchase_orders FOR EACH ROW EXECUTE FUNCTION hms_guard_purchase_order_business_date();"))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_purchase_receipt_business_date()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE current_date_value date;
            BEGIN
                SELECT current_business_date INTO current_date_value FROM business_date_state WHERE id = 1 FOR UPDATE;
                IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Business date is not initialized'; END IF;
                IF NEW.business_date <> current_date_value THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Goods receipt date %s is not the current business date %s', NEW.business_date, current_date_value);
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("CREATE TRIGGER trg_goods_receipt_business_date_guard BEFORE INSERT ON goods_receipts FOR EACH ROW EXECUTE FUNCTION hms_guard_purchase_receipt_business_date();"))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_purchasing_transition()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_TABLE_NAME = 'purchase_orders' AND NEW.status <> OLD.status THEN
                    IF NOT ((OLD.status='draft' AND NEW.status IN ('approved','cancelled')) OR
                            (OLD.status='approved' AND NEW.status IN ('partially_received','received')) OR
                            (OLD.status='partially_received' AND NEW.status='received')) THEN
                        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Invalid purchase order transition %s -> %s', OLD.status, NEW.status);
                    END IF;
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("CREATE TRIGGER trg_purchase_order_lifecycle BEFORE UPDATE OF status ON purchase_orders FOR EACH ROW EXECUTE FUNCTION hms_guard_purchasing_transition();"))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_purchasing_immutability()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Posted purchasing history is immutable';
            END; $$;
        """))
        for table_name, trig in [("goods_receipts", "goods_receipt_immutable"), ("goods_receipt_lines", "goods_receipt_line_immutable")]:
            op.execute(sa.text(f"CREATE TRIGGER trg_{trig}_update BEFORE UPDATE ON {table_name} FOR EACH ROW EXECUTE FUNCTION hms_guard_purchasing_immutability();"))
            op.execute(sa.text(f"CREATE TRIGGER trg_{trig}_delete BEFORE DELETE ON {table_name} FOR EACH ROW EXECUTE FUNCTION hms_guard_purchasing_immutability();"))


def downgrade() -> None:
    raise RuntimeError("Purchasing receipts are append-only; restore a verified backup instead of destructively rolling back procurement history.")
