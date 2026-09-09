"""Add transactional housekeeping and maintenance controls."""
from alembic import op
import sqlalchemy as sa

revision = "0016_housekeeping_integrity"
down_revision = "0015_purchasing_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "housekeeping_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_no", sa.String(40), nullable=False),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("task_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("assigned_to", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("completed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("task_no", name="uq_housekeeping_tasks_task_no"),
        sa.CheckConstraint("status IN ('pending','in_progress','completed','cancelled')", name="ck_housekeeping_tasks_status"),
        sa.CheckConstraint("priority IN ('low','normal','high','urgent')", name="ck_housekeeping_tasks_priority"),
    )
    op.create_index("ix_housekeeping_tasks_task_no", "housekeeping_tasks", ["task_no"], unique=True)
    op.create_index("ix_housekeeping_tasks_room_id", "housekeeping_tasks", ["room_id"])
    op.create_index("ix_housekeeping_tasks_business_date", "housekeeping_tasks", ["business_date"])
    op.create_index("ix_housekeeping_tasks_status", "housekeeping_tasks", ["status"])

    op.create_table(
        "maintenance_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("block_no", sa.String(40), nullable=False),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("reported_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("resolved_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("block_no", name="uq_maintenance_blocks_block_no"),
        sa.CheckConstraint("status IN ('active','resolved','cancelled')", name="ck_maintenance_blocks_status"),
        sa.CheckConstraint("severity IN ('normal','high','critical')", name="ck_maintenance_blocks_severity"),
    )
    op.create_index("ix_maintenance_blocks_block_no", "maintenance_blocks", ["block_no"], unique=True)
    op.create_index("ix_maintenance_blocks_room_id", "maintenance_blocks", ["room_id"])
    op.create_index("ix_maintenance_blocks_business_date", "maintenance_blocks", ["business_date"])
    op.create_index("ix_maintenance_blocks_status", "maintenance_blocks", ["status"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("""
            CREATE UNIQUE INDEX uq_housekeeping_active_room
            ON housekeeping_tasks(room_id)
            WHERE status IN ('pending','in_progress');
        """))
        op.execute(sa.text("""
            CREATE UNIQUE INDEX uq_active_maintenance_room
            ON maintenance_blocks(room_id)
            WHERE status = 'active';
        """))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_housekeeping_business_date()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE current_date_value date;
            BEGIN
                SELECT current_business_date INTO current_date_value FROM business_date_state WHERE id=1 FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Business date is not initialized';
                END IF;
                IF NEW.business_date <> current_date_value THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Housekeeping task date %s is not current business date %s', NEW.business_date, current_date_value);
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("CREATE TRIGGER trg_housekeeping_business_date BEFORE INSERT ON housekeeping_tasks FOR EACH ROW EXECUTE FUNCTION hms_guard_housekeeping_business_date();"))
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_maintenance_business_date()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE current_date_value date;
            BEGIN
                SELECT current_business_date INTO current_date_value FROM business_date_state WHERE id=1 FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Business date is not initialized';
                END IF;
                IF NEW.business_date <> current_date_value THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Maintenance block date %s is not current business date %s', NEW.business_date, current_date_value);
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("CREATE TRIGGER trg_maintenance_business_date BEFORE INSERT ON maintenance_blocks FOR EACH ROW EXECUTE FUNCTION hms_guard_maintenance_business_date();"))

        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_room_maintenance_consistency()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.status IN ('occupied','reserved','available','dirty')
                   AND EXISTS (SELECT 1 FROM maintenance_blocks WHERE room_id=NEW.id AND status='active') THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Room %s has an active maintenance block', NEW.id);
                END IF;
                IF NEW.status='out_of_order' AND TG_OP='UPDATE' THEN
                    RETURN NEW;
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("CREATE TRIGGER trg_room_maintenance_consistency BEFORE UPDATE OF status ON rooms FOR EACH ROW EXECUTE FUNCTION hms_guard_room_maintenance_consistency();"))

        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_maintenance_block_sync_room()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP='INSERT' AND NEW.status='active' THEN
                    IF NEW.business_date = (SELECT current_business_date FROM business_date_state WHERE id=1) THEN
                        UPDATE rooms SET status='out_of_order' WHERE id=NEW.room_id AND status NOT IN ('occupied','reserved');
                        IF NOT FOUND THEN
                            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Maintenance block cannot be created for occupied or reserved room';
                        END IF;
                    END IF;
                ELSIF TG_OP='UPDATE' AND OLD.status='active' AND NEW.status='resolved' THEN
                    UPDATE rooms SET status='dirty' WHERE id=NEW.room_id AND status='out_of_order';
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("CREATE TRIGGER trg_maintenance_block_sync_room AFTER INSERT OR UPDATE OF status ON maintenance_blocks FOR EACH ROW EXECUTE FUNCTION hms_maintenance_block_sync_room();"))

        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION hms_guard_housekeeping_transition()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.status <> OLD.status THEN
                    IF NOT ((OLD.status='pending' AND NEW.status IN ('in_progress','cancelled')) OR
                            (OLD.status='in_progress' AND NEW.status IN ('completed','cancelled')) OR
                            OLD.status=NEW.status) THEN
                        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE=format('Invalid housekeeping task transition %s -> %s', OLD.status, NEW.status);
                    END IF;
                END IF;
                RETURN NEW;
            END; $$;
        """))
        op.execute(sa.text("CREATE TRIGGER trg_housekeeping_transition BEFORE UPDATE OF status ON housekeeping_tasks FOR EACH ROW EXECUTE FUNCTION hms_guard_housekeeping_transition();"))


def downgrade() -> None:
    raise RuntimeError("Housekeeping and maintenance history is operationally authoritative; restore a verified backup instead of destructive rollback.")
