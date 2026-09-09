"""Add inventory operation integrity guards."""
from alembic import op
import sqlalchemy as sa

revision = "0014_inventory_control"
down_revision = "0013_restaurant_pos_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_no", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("operation_type", sa.String(length=30), nullable=False),
        sa.Column("stock_item_id", sa.Integer(), sa.ForeignKey("stock_items.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("operation_no", name="uq_stock_operations_operation_no"),
        sa.UniqueConstraint("idempotency_key", name="uq_stock_operations_idempotency_key"),
    )
    op.create_index("ix_stock_operations_operation_no", "stock_operations", ["operation_no"], unique=True)
    op.create_index("ix_stock_operations_idempotency_key", "stock_operations", ["idempotency_key"], unique=True)
    op.create_index("ix_stock_operations_idempotency_fingerprint", "stock_operations", ["idempotency_fingerprint"])
    op.create_index("ix_stock_operations_business_date", "stock_operations", ["business_date"])
    op.create_index("ix_stock_operations_operation_type", "stock_operations", ["operation_type"])
    op.create_index("ix_stock_operations_stock_item_id", "stock_operations", ["stock_item_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(
            "ALTER TABLE stock_items DROP CONSTRAINT IF EXISTS ck_stock_items_nonnegative_on_hand"
        ))
        op.execute(sa.text(
            "ALTER TABLE stock_items ADD CONSTRAINT ck_stock_items_nonnegative_on_hand CHECK (on_hand >= 0)"
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
            CREATE TRIGGER trg_stock_operation_immutable_update
            BEFORE UPDATE ON stock_operations FOR EACH ROW EXECUTE FUNCTION hms_guard_stock_operation_immutable();
        """))
        op.execute(sa.text("""
            CREATE TRIGGER trg_stock_operation_immutable_delete
            BEFORE DELETE ON stock_operations FOR EACH ROW EXECUTE FUNCTION hms_guard_stock_operation_immutable();
        """))


def downgrade() -> None:
    raise RuntimeError("Inventory history is append-only; restore a verified backup instead of destructively rolling back stock controls.")
