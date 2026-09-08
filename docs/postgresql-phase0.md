# PostgreSQL + Phase A migration

La Serene HMS now supports PostgreSQL through `HMS_DATABASE_URL` and uses Alembic as the production schema migration mechanism.

## Fresh PostgreSQL installation

Create the empty `la_serene_hms` database and set:

```text
HMS_DATABASE_URL=postgresql+psycopg://la_serene_hms:CHANGE_ME@127.0.0.1:5432/la_serene_hms
HMS_SECRET_KEY=CHANGE_ME
```

Then from `apps/api` run:

```text
alembic upgrade head
```

Start FastAPI only after the database migration has completed.

## Existing hotel SQLite installation

1. Keep the original SQLite database as an untouched safety copy.
2. Create the empty PostgreSQL database.
3. Set `HMS_DATABASE_URL` to the PostgreSQL connection string.
4. Run `alembic upgrade head`.
5. Set `HMS_SQLITE_PATH` to the original SQLite file and run:

```text
python ../../scripts/migrate_sqlite_to_postgres.py
```

6. Verify room, guest, reservation, folio and payment totals before the first live shift.
7. Keep the SQLite source and the generated PostgreSQL backup until the hotel signs off on the migration.

The migration utility preserves primary keys and resets PostgreSQL identity sequences after copying data. It refuses to run against a PostgreSQL database that already contains application rows.

## Phase A normalized records

The database now separates operational concerns without deleting the existing `stays` data:

- `stay_occupants` — multiple occupants per room stay, with a primary occupant.
- `stay_rate_segments` — date-ranged rates, discounts and rate plans.
- `deposit_transactions` — received, applied, refunded and adjusted deposit movements.
- `room_moves` — immutable room-move history.
- `reservation_splits` — source/new reservation relationships for room-level splits.
- `business_date_state` — the hotel business-date state used as the basis for future night-audit locking.

Existing `stays` fields remain during the transition so older screens and data are not broken. New workflows populate the normalized records automatically.
