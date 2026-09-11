"""Document stacked percentage + fixed discounts.

No schema change is required: existing discount_percent and discount_amount
columns already store the percentage and total monetary discount respectively.
This revision records the semantic change in the migration history so fresh
and existing databases share the same application contract.
"""
from alembic import op

revision = "0017_stacked_discounts"
down_revision = "0016_housekeeping_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
