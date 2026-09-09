# Phase K12 — Database Lifecycle & Upgrade Safety

K12 defines the controlled production database upgrade path for La Serene HMS.

## Safety contract

Production schema changes follow this order:

1. Validate the production PostgreSQL environment.
2. Create a mandatory PostgreSQL backup using the existing Phase J backup system.
3. Inspect the current Alembic revision and application head.
4. Stop the HMS API service for a controlled maintenance window.
5. Run `alembic upgrade head`.
6. Verify the resulting Alembic revision and application migration guard.
7. Restart the HMS API service.
8. Verify `/api/ready` returns HTTP 200.

The K12 script delegates backup creation to the existing Phase J backup system and does not create a second backup system.

## Windows production upgrade

Run from an elevated PowerShell session on the HMS server:

```powershell
.\scripts\windows\upgrade-production-db.ps1
```

A pre-upgrade backup failure aborts the migration. The script refuses non-PostgreSQL production URLs, does not print the database password, and refuses to migrate while the HMS service is running.

## Startup protection

`app.migration_guard.check_database_at_head()` runs during production application startup. The service fails closed when the database is not PostgreSQL, the migration state cannot be read, multiple current revisions or heads exist, or the current revision differs from the application head.

## Idempotency

`alembic upgrade head` is idempotent when the database is already current. The migration guard must continue to report exactly one application head.

## Rollback / recovery

Alembic downgrade is **not** an automatic production rollback mechanism. A failed or incompatible release should use the verified Phase J/K11 backup and isolated recovery workflow. Restore into a dedicated recovery database first, validate integrity, and only then plan any production restoration.

## Deployment order

For application releases containing schema changes:

1. Build and test the application.
2. Deploy the release files without starting incompatible traffic.
3. Run the K12 database lifecycle script.
4. Confirm `/api/ready` is healthy.
5. Complete the application smoke test.
6. Keep the pre-upgrade backup until the release has passed acceptance testing.
