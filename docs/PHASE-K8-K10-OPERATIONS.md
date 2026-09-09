# Phase K8-K10 — Production Operations

This runbook covers health monitoring, persistent logging, and automated PostgreSQL backups for a Windows production installation.

## K8 — Health monitoring

The production service exposes:

- `GET /api/health` — liveness check; confirms the API process is serving requests.
- `GET /api/ready` — readiness check; executes `SELECT 1` against PostgreSQL and returns HTTP 503 when the database is unavailable.

From an elevated PowerShell prompt on the HMS server:

```powershell
C:\LaSereneHMS\scripts\windows\check-health.ps1
```

A successful result must report both liveness and readiness as HTTP 200.

## K9 — Persistent logging

Production API logs are written to:

`C:\LaSereneHMS\logs\api.log`

The application uses a rotating file handler with a 10 MiB maximum file size and 14 rotated files. NSSM continues to capture service stdout/stderr under the same `logs` directory. This gives operators both application logs and service-level startup/failure diagnostics.

Do not store credentials, `.env` files, or backup artifacts in Git.

## K10 — Automated PostgreSQL backups

The backup tooling from Phase J remains authoritative. K10 adds a Windows scheduler runner that:

1. Reads `HMS_DATABASE_URL` from the production `.env` without printing it.
2. Creates a PostgreSQL custom-format dump.
3. Captures business-date and Alembic metadata before and after the dump.
4. Rejects the artifact if live metadata changes during the dump.
5. Writes a SHA-256 manifest.
6. Immediately verifies the dump checksum and manifest.
7. Retains the newest configured number of backup sets.
8. Writes operational success/failure messages to `backups\backup.log`.

### Register the scheduled task

Run as an administrator:

```powershell
C:\LaSereneHMS\scripts\windows\install-backup-task.ps1
```

The installer prompts for the Windows account password used by Task Scheduler. The password is not written to the repository or to the task command line.

Default schedule: **02:00 every day**.

Default backup directory:

`C:\LaSereneHMS\backups`

Default retention: **7 verified backup sets**.

### Manual backup test

```powershell
C:\LaSereneHMS\scripts\windows\run-backup.ps1
```

A successful run prints a JSON result with `status: verified` and records the same result in `backups\backup.log`.

### Scheduled-task test

```powershell
Start-ScheduledTask -TaskName "La Serene HMS - PostgreSQL Backup"
Get-ScheduledTaskInfo -TaskName "La Serene HMS - PostgreSQL Backup"
```

Confirm that the task completes successfully and that a new `.dump` plus `.manifest.json` pair exists in the backup directory.

### Recovery

K10 does not replace the Phase J recovery procedure. To test or perform a restore, use the existing `ops.backup.backup restore-verify` workflow against a clean PostgreSQL database. A restore is not considered successful until the business-date singleton, Alembic revision, checksum, and financial-ledger balance checks pass.

## Operational acceptance

- `/api/health` returns HTTP 200 after service startup.
- `/api/ready` returns HTTP 200 only when PostgreSQL is reachable.
- API logs survive process restarts and rotate automatically.
- A scheduled backup produces a dump and manifest without exposing credentials.
- The backup checksum is verified before the scheduled runner reports success.
- Backup failures are persisted to `backups\backup.log`.
- Backup retention removes older dump/manifest pairs.
- No backup or production-secret artifact is committed to Git.
