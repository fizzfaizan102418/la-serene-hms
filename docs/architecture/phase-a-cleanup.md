# Phase A production cleanup

The Phase A production cleanup establishes three boundaries:

- Alembic owns the production schema lifecycle.
- `business_date_state.current_business_date` owns hotel operational dates.
- API routers have a single registration path.

The existing SQLite compatibility layer remains transitional and does not change PostgreSQL schema ownership.
