# Business date authority

La Serene HMS operational dates are controlled by the `business_date_state.current_business_date` value, not the Windows/system calendar.

Alembic revision `0008_seed_business_date` initializes the state row for new or migrated databases. Runtime operational services read the state through `app.business_date.get_current_business_date()`.

A fallback to `date.today()` remains available only for legacy SQLite compatibility while installations are migrated. Production PostgreSQL environments must have an initialized business-date row through Alembic/night-audit operations.

## Rules

- Front-desk arrivals, departures, walk-ins, and dashboard operational dates use the business date.
- Night audit uses the business date state as its source of truth.
- Financial transactions continue to carry their explicit business date.
- The system must never silently advance the business date because the Windows clock crossed midnight.
- Closing and opening a business date belong to the Night Audit workflow and must be audited.
