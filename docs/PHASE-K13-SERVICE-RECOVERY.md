# Phase K13 — Service Recovery & Operational Resilience

## Purpose

Phase K13 proves that the Windows production runtime can detect, report, and recover from common service and database failures without duplicating the authoritative K12 database lifecycle, Phase J backup, or K11 disaster-recovery paths.

## K13 acceptance matrix

| Area | Expected result |
| --- | --- |
| API process crash | NSSM restarts the API automatically |
| Intentional API service stop | Service remains stopped until explicitly started |
| API readiness with healthy PostgreSQL | `/api/ready` returns HTTP 200 |
| API readiness with unavailable PostgreSQL | `/api/ready` returns HTTP 503 |
| PostgreSQL recovery | `/api/ready` returns HTTP 200 again |
| Readiness error detail | Does not expose credentials or connection strings |
| API logs | stdout/stderr persist across service restarts and remain rotated |
| Scheduled backup | Windows task remains configured for automatic execution |
| Backup failure | Task fails without printing database credentials |
| Host reboot | API and backup task are configured for automatic startup/scheduling |
| Recovery procedure | Operator can distinguish transient outage from migration/restore event |

## Controlled verification

### 1. API process recovery

Run from an elevated PowerShell session during an approved maintenance window:

```powershell
.\scripts\windows\verify-api-recovery.ps1 -ConfirmUnexpectedTermination
```

The verifier first requires `/api/ready` HTTP 200, identifies the Windows service process, deliberately terminates that process, and waits for NSSM to create a replacement process. It then requires `/api/ready` HTTP 200 again.

This is an intentional process termination test. It must never be run against an uncontrolled workstation or during active guest operations.

### 2. PostgreSQL outage and recovery

First identify the installed PostgreSQL Windows service name, then run:

```powershell
.\scripts\windows\verify-postgresql-recovery.ps1 -PostgresServiceName "postgresql-x64-17" -ConfirmDatabaseInterruption
```

The exact service name may differ by installation. The verifier requires healthy readiness first, stops PostgreSQL, requires `/api/ready` HTTP 503, starts PostgreSQL again, and requires HTTP 200.

No Alembic migration, backup, restore, or schema change is performed by this test.

### 3. Production recovery prerequisites

Run:

```powershell
.\scripts\windows\verify-production-recovery.ps1
```

The verifier checks:

- production `.env` exists;
- API service is configured for automatic startup;
- scheduled PostgreSQL backup task exists and is not disabled;
- persistent API stdout/stderr logs exist;
- `/api/ready` is HTTP 200.

### 4. Reboot verification

A reboot is deliberately not automated by CI or by a normal deployment script. It is an operator-controlled production test during a maintenance window.

Before reboot:

1. Confirm the API is healthy with `verify-production-recovery.ps1`.
2. Confirm the most recent scheduled backup completed successfully.
3. Confirm an independent copy of critical backups exists when required by the deployment policy.
4. Record the current business date and operational state.

After reboot:

1. Run `verify-production-recovery.ps1`.
2. Confirm the API service is running.
3. Confirm `/api/ready` returns HTTP 200.
4. Confirm the scheduled backup task is enabled and has a valid next run time.
5. Confirm API logs continue from the pre-reboot log history.
6. If readiness is not restored, follow the operator recovery sequence below instead of immediately changing the database schema.

## Operator recovery sequence

### A. API is unavailable

1. Check the API Windows service state.
2. Check `/api/ready` locally.
3. Inspect `logs\api.stderr.log` and `logs\api.stdout.log`.
4. Check PostgreSQL service state.
5. If PostgreSQL is healthy but the API service is stopped, start the API service explicitly.
6. If the API repeatedly crashes, stop troubleshooting the crash loop and inspect the persistent logs and Windows Event Viewer.

### B. PostgreSQL is unavailable

1. Do not run Alembic migrations.
2. Do not run restore operations against production.
3. Confirm the PostgreSQL Windows service state.
4. Restore PostgreSQL service availability through the normal Windows service procedure.
5. Verify `/api/ready` returns HTTP 200.
6. Only after the service is healthy should normal HMS operations resume.

### C. Schema is reported as pending/divergent

Do not treat a schema failure as a transient outage. K12 remains authoritative for production database lifecycle and migration safety. Use the K12 upgrade procedure, including its mandatory pre-upgrade Phase J backup, rather than restarting the service repeatedly.

### D. Data corruption or disaster recovery is suspected

Do not improvise a production restore. Phase J remains authoritative for backup verification and restore mechanics, while K11 remains authoritative for guarded recovery into an isolated recovery target.

## Backup failure handling

The scheduled Windows backup runner reads `HMS_DATABASE_URL` only into the process environment and removes it before exit. Failure messages are intentionally generic. Operators should inspect the backup log and Windows Task Scheduler history rather than echoing the production `.env` file or printing the database URL.

Never paste production credentials into tickets, chat, issue reports, or command output.

## Boundaries

- **K12:** production schema lifecycle, migration-head guard, upgrade safety, and service stop/start around migrations.
- **Phase J:** PostgreSQL backup creation, manifest/SHA-256 verification, restore verification, retention, and backup round-trip validation.
- **K11:** guarded disaster recovery and isolated restore verification.
- **K13.1:** NSSM API process restart behavior and persistent service logging.
- **K13.2:** application-level database readiness signaling.

K13 adds verification and operational recovery checks; it does not create a second migration, backup, or restore implementation.

## Go-live gate

K13 is complete only when all automated CI and Windows PowerShell syntax checks are green, the controlled API recovery test passes on the target Windows host, the controlled PostgreSQL outage/recovery test passes, the production recovery prerequisite check passes, and the reboot test has been completed during an approved maintenance window.
