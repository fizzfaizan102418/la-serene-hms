"""Expand hotel expenses into a first-class operational expense module."""
from datetime import date, datetime

from alembic import op
import sqlalchemy as sa

revision = "0018_hotel_expenses"
down_revision = "0017_stacked_discounts"
branch_labels = None
depends_on = None


_EXPENSE_COLUMNS = {
    "expense_date": sa.Column("expense_date", sa.Date(), nullable=True),
    "expense_no": sa.Column("expense_no", sa.String(length=40), nullable=True),
    "category": sa.Column("category", sa.String(length=80), nullable=True),
    "paid_to": sa.Column("paid_to", sa.String(length=160), nullable=True),
    "reference": sa.Column("reference", sa.String(length=100), nullable=True),
    "department": sa.Column("department", sa.String(length=60), nullable=True),
    "notes": sa.Column("notes", sa.Text(), nullable=True),
    "created_by": sa.Column("created_by", sa.Integer(), nullable=True),
    "status": sa.Column("status", sa.String(length=20), nullable=True),
}


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _has_unique_expense_no(inspector) -> bool:
    for index in inspector.get_indexes("expenses"):
        if index.get("unique") and index.get("column_names") == ["expense_no"]:
            return True
    for constraint in inspector.get_unique_constraints("expenses"):
        if constraint.get("column_names") == ["expense_no"]:
            return True
    return False


def _has_created_by_fk(inspector) -> bool:
    for fk in inspector.get_foreign_keys("expenses"):
        if (
            fk.get("referred_table") == "users"
            and fk.get("constrained_columns") == ["created_by"]
            and fk.get("referred_columns") == ["id"]
        ):
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("expenses")}

    # 0001_initial_hms uses the current ORM metadata, so a fresh database may
    # already contain these columns. Older databases at 0017 will not.
    for name, column in _EXPENSE_COLUMNS.items():
        if name not in existing_columns:
            op.add_column("expenses", column)

    inspector = sa.inspect(bind)
    rows = bind.execute(sa.text("SELECT id, created_at FROM expenses")).mappings().all()
    for row in rows:
        created = row["created_at"]
        if isinstance(created, datetime):
            expense_date = created.date()
        elif isinstance(created, date):
            expense_date = created
        elif isinstance(created, str):
            try:
                expense_date = datetime.fromisoformat(created).date()
            except ValueError:
                expense_date = date.today()
        else:
            expense_date = date.today()

        bind.execute(
            sa.text(
                "UPDATE expenses SET "
                "expense_date = COALESCE(expense_date, :expense_date), "
                "expense_no = COALESCE(expense_no, :expense_no), "
                "category = COALESCE(category, 'Miscellaneous'), "
                "department = COALESCE(department, 'Hotel'), "
                "status = COALESCE(status, 'posted') "
                "WHERE id = :id"
            ),
            {
                "expense_date": expense_date,
                "expense_no": f"EXP-{row['id']:06d}",
                "id": row["id"],
            },
        )

    inspector = sa.inspect(bind)
    if bind.dialect.name != "sqlite":
        if not _has_unique_expense_no(inspector):
            op.create_unique_constraint("uq_expenses_expense_no", "expenses", ["expense_no"])
        if not _has_created_by_fk(inspector):
            op.create_foreign_key(
                "fk_expenses_created_by_users",
                "expenses",
                "users",
                ["created_by"],
                ["id"],
            )
        for column_name in ("expense_date", "expense_no", "category", "department", "status"):
            op.alter_column("expenses", column_name, nullable=False)
    elif not _has_unique_expense_no(inspector):
        op.create_index("uq_expenses_expense_no", "expenses", ["expense_no"], unique=True)

    inspector = sa.inspect(bind)
    for index_name, columns in (
        ("ix_expenses_expense_date", ["expense_date"]),
        ("ix_expenses_category", ["category"]),
        ("ix_expenses_department", ["department"]),
        ("ix_expenses_status", ["status"]),
    ):
        if not _has_index(inspector, "expenses", index_name):
            op.create_index(index_name, "expenses", columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for index_name in (
        "ix_expenses_status",
        "ix_expenses_department",
        "ix_expenses_category",
        "ix_expenses_expense_date",
    ):
        if _has_index(inspector, "expenses", index_name):
            op.drop_index(index_name, table_name="expenses")

    inspector = sa.inspect(bind)
    if bind.dialect.name == "sqlite":
        if _has_index(inspector, "expenses", "uq_expenses_expense_no"):
            op.drop_index("uq_expenses_expense_no", table_name="expenses")
    else:
        if _has_created_by_fk(inspector):
            op.drop_constraint("fk_expenses_created_by_users", "expenses", type_="foreignkey")
        if any(
            constraint.get("name") == "uq_expenses_expense_no"
            for constraint in inspector.get_unique_constraints("expenses")
        ):
            op.drop_constraint("uq_expenses_expense_no", "expenses", type_="unique")

    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("expenses")}
    for column_name in (
        "status",
        "created_by",
        "notes",
        "department",
        "reference",
        "paid_to",
        "category",
        "expense_no",
        "expense_date",
    ):
        if column_name in existing_columns:
            op.drop_column("expenses", column_name)
