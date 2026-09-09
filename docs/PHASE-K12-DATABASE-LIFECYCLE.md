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

The K12 script does not create a second backup or recovery implementation; it delegates backup creation to `scripts/windows/run-backup.ps1` and the Phase J `ops.backup` workflow.

## Windows production upgrade

Run from an elevated PowerShell session on the HMS server:

```powershell
.\scripts\windows\upgrade-production-db.ps1
```

Optional parameters:

```powershell
.\scripts\windows\upgrade-production-db.ps1 `
  -InstallRoot C:\LaSereneHMS `
  -BackupDir C:\LaSereneHMS\backups `
  -Retain 14 `
  -ServiceName LaSereneHMSApi
```

A pre-upgrade backup failure aborts the migration. If the migration fails, the script leaves the service stopped rather than silently serving an uncertain schema.

The script refuses non-PostgreSQL production URLs and does not print the database password.

## Startup protection

`app.migration_guard.check_database_at_head()` runs during production application startup. The service fails closed when:

- the database is not PostgreSQL;
- the `alembic_version` table is missing or unreadable;
- more than one current revision exists;
- more than one migration head exists; or
- the current database revision differs from the application head.

This prevents a production API process from starting against a pending or divergent schema.

## Idempotency

After an upgrade, rerunning the lifecycle script is safe with respect to Alembic: `alembic upgrade head` reports no additional migration when the database is already current. The migration guard must continue to report the single application head.

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

The production startup guard is the final safety net; it is not a substitute for the controlled upgrade procedure.
