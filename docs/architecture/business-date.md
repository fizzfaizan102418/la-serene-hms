# Business date authority

La Serene HMS operational dates are controlled by `business_date_state.current_business_date`, not by the Windows/system calendar.

Alembic revision `0008_seed_business_date` initializes the state row for new or migrated databases. Runtime services read the state through `app.business_date.get_current_business_date()`.

The `date.today()` fallback is explicitly limited to legacy SQLite compatibility. Production PostgreSQL installations must have an initialized business-date row through Alembic and the Night Audit workflow.

## Rules

- Front-desk arrivals, departures, walk-ins, and dashboard operational dates use the business date.
- Night audit uses the business-date state as its source of truth.
- Financial transactions continue to carry their explicit business date.
- The business date must never advance merely because the Windows clock crossed midnight.
- Opening, closing, and advancing the business date are audited operational actions.
