"""Expand hotel expenses into a first-class operational expense module."""
from datetime import date, datetime

from alembic import op
import sqlalchemy as sa

revision = "0018_hotel_expenses"
down_revision = "0017_stacked_discounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expenses", sa.Column("expense_date", sa.Date(), nullable=True))
    op.add_column("expenses", sa.Column("expense_no", sa.String(length=40), nullable=True))
    op.add_column("expenses", sa.Column("category", sa.String(length=80), nullable=True))
    op.add_column("expenses", sa.Column("paid_to", sa.String(length=160), nullable=True))
    op.add_column("expenses", sa.Column("reference", sa.String(length=100), nullable=True))
    op.add_column("expenses", sa.Column("department", sa.String(length=60), nullable=True))
    op.add_column("expenses", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("expenses", sa.Column("created_by", sa.Integer(), nullable=True))
    op.add_column("expenses", sa.Column("status", sa.String(length=20), nullable=True))

    bind = op.get_bind()
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
            sa.text("UPDATE expenses SET expense_date = :expense_date, expense_no = :expense_no, category = 'Miscellaneous', department = 'Hotel', status = 'posted' WHERE id = :id"),
            {"expense_date": expense_date, "expense_no": f"EXP-{row['id']:06d}", "id": row["id"]},
        )

    if bind.dialect.name == "sqlite":
        op.create_index("uq_expenses_expense_no", "expenses", ["expense_no"], unique=True)
    else:
        op.create_unique_constraint("uq_expenses_expense_no", "expenses", ["expense_no"])
        op.create_foreign_key("fk_expenses_created_by_users", "expenses", "users", ["created_by"], ["id"])
        op.alter_column("expenses", "expense_date", nullable=False)
        op.alter_column("expenses", "expense_no", nullable=False)
        op.alter_column("expenses", "category", nullable=False)
        op.alter_column("expenses", "department", nullable=False)
        op.alter_column("expenses", "status", nullable=False)

    op.create_index("ix_expenses_expense_date", "expenses", ["expense_date"])
    op.create_index("ix_expenses_category", "expenses", ["category"])
    op.create_index("ix_expenses_department", "expenses", ["department"])
    op.create_index("ix_expenses_status", "expenses", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_expenses_status", table_name="expenses")
    op.drop_index("ix_expenses_department", table_name="expenses")
    op.drop_index("ix_expenses_category", table_name="expenses")
    op.drop_index("ix_expenses_expense_date", table_name="expenses")
    if bind.dialect.name == "sqlite":
        op.drop_index("uq_expenses_expense_no", table_name="expenses")
    else:
        op.drop_constraint("fk_expenses_created_by_users", "expenses", type_="foreignkey")
        op.drop_constraint("uq_expenses_expense_no", "expenses", type_="unique")
    for column in ("status", "created_by", "notes", "department", "reference", "paid_to", "category", "expense_no", "expense_date"):
        op.drop_column("expenses", column)
