# Production architecture cleanup

This cleanup keeps the existing API surface stable while making infrastructure responsibilities explicit.

1. Alembic is the production schema authority; application startup does not call `Base.metadata.create_all()`.
2. The hotel business date is stored in `business_date_state` and accessed through the business-date service.
3. Legacy SQLite compatibility remains isolated to migration compatibility paths and must not become the production PostgreSQL schema mechanism.
4. Router ownership is centralized so each API operation is registered exactly once.
