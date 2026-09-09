# Phase K3 — Production Database Initialization and Alembic Migration Verification

## Purpose

K3 makes the production HMS schema reproducibly deployable from an empty PostgreSQL database. K2 prepares PostgreSQL and the dedicated application database/user; K3 applies and verifies the application's Alembic migration chain.

## Scope

K3 covers:

- production PostgreSQL connection through `HMS_DATABASE_URL`;
- migration execution from an empty database;
- verification that the migration chain reaches a single current head;
- repeat execution of `alembic upgrade head` as an idempotency check;
- SQLite migration compatibility retained for development/CI;
- application startup against the migrated database;
- a documented production initialization procedure.

K3 does **not** import hotel/business data. Data migration or legacy-data import is a separate controlled operation.

## Production initialization

After K2 has prepared PostgreSQL and the `la_serene_hms` database:

1. Check out the intended HMS release/commit.
2. Create the production environment configuration outside Git.
3. Set:

```text
HMS_ENVIRONMENT=production
HMS_DATABASE_URL=postgresql+psycopg://la_serene_hms_app:<URL_ENCODED_PASSWORD>@127.0.0.1:5432/la_serene_hms
HMS_SECRET_KEY=<STRONG_RANDOM_SECRET>
HMS_TOKEN_EXPIRE_MINUTES=480
```

4. From `apps/api`, install the pinned API dependencies:

```powershell
python -m pip install -r requirements.txt
```

5. Verify the migration chain without changing the database:

```powershell
alembic heads
```

There should be one migration head for the release.

6. Initialize the schema:

```powershell
alembic upgrade head
```

7. Verify the applied migration state:

```powershell
alembic current
```

The current revision must match the release's migration head.

8. Run the command a second time:

```powershell
alembic upgrade head
```

The second execution should be a no-op. If it attempts to alter already-created objects or fails, stop deployment and investigate the migration chain before starting the application.

9. Start the FastAPI service only after migration completion has succeeded.

## Safety rules

- Never run production migrations against the wrong `HMS_DATABASE_URL`.
- Never commit production passwords or JWT secrets.
- Do not use `Base.metadata.create_all()` as a substitute for Alembic in production.
- Do not manually edit the production schema to bypass a failed migration.
- Back up the production database before applying future schema upgrades after K3.
- Keep business-data import separate from schema initialization.

## CI verification

The CI pipeline uses a fresh PostgreSQL service for every run. It now verifies:

1. the application imports successfully;
2. `alembic upgrade head` initializes the empty PostgreSQL database;
3. `alembic current` reports the resulting migration state;
4. a second `alembic upgrade head` is safe and idempotent;
5. the API test suite and PostgreSQL smoke tests pass;
6. the migration chain remains compatible with SQLite;
7. PostgreSQL backup/restore round-trip verification still passes.

This is intentionally stronger than merely testing application queries against an already-initialized database.

## Failure handling

If `alembic upgrade head` fails:

1. Do not start or expose the production application.
2. Capture the migration error and current revision.
3. Confirm `HMS_DATABASE_URL` points to the intended database.
4. Inspect the failing migration and database state.
5. Restore the database from the latest verified backup if the failed deployment changed production state and recovery is required.
6. Correct the migration or deployment procedure in source control.
7. Re-run CI against an empty PostgreSQL database before retrying production.

## K3 acceptance checklist

- [ ] Production uses `HMS_DATABASE_URL`.
- [ ] PostgreSQL is the production database engine.
- [ ] Migration chain has one head.
- [ ] Empty PostgreSQL database reaches migration head with `alembic upgrade head`.
- [ ] `alembic current` confirms the expected head.
- [ ] Re-running `alembic upgrade head` is a no-op.
- [ ] API imports successfully against production configuration.
- [ ] PostgreSQL smoke tests pass.
- [ ] SQLite compatibility remains intact for development/CI.
- [ ] No production data is imported as part of schema initialization.
- [ ] Production migration procedure is documented.
