# Phase K13.1 — API Service Recovery

## Objective

Make the Windows FastAPI service resilient to an unexpected API process termination without changing the application database lifecycle or backup/recovery responsibilities.

## Recovery contract

The production API is installed as a Windows service through NSSM.

The service configuration guarantees:

1. Windows starts `LaSereneHMSApi` automatically at boot.
2. A normal service stop remains stopped.
3. An unexpected API process exit uses NSSM's `Default Restart` action.
4. Restart attempts wait 5 seconds and are throttled at 5 seconds to avoid a tight crash loop.
5. NSSM uses bounded console/window/thread stop methods for controlled shutdown.
6. API stdout/stderr remain in the persistent `logs` directory and continue across process restarts.
7. PostgreSQL can be declared as a Windows service dependency when its service name is supplied.

## Important boundary

K13 service recovery does **not** attempt to repair PostgreSQL, alter the database schema, or restore a backup automatically.

- K12 owns database lifecycle and migration safety.
- Phase J owns backup creation and verification.
- K11 owns guarded disaster recovery and restore verification.
- `/api/ready` remains the authoritative application-level database readiness signal.

If PostgreSQL is unavailable, the API process may remain running while `/api/ready` returns HTTP 503. When PostgreSQL becomes available again, `/api/ready` should return HTTP 200 without requiring a database restore or schema migration.

## Installation

Run the existing installer from the production deployment root:

```powershell
.\scripts\windows\install-api-service.ps1 `
  -InstallRoot 'C:\LaSereneHMS' `
  -PostgresServiceName 'postgresql-x64-17'
```

The PostgreSQL service name is optional. If it is supplied and exists, NSSM adds it as a service dependency. If it is omitted, application-level readiness remains responsible for detecting database availability.

## Operator verification

After installation, verify:

```powershell
Get-Service -Name 'LaSereneHMSApi'
```

Then terminate only the API child process during a controlled recovery test and confirm that NSSM starts it again after the configured delay. Do not stop the Windows service itself for this test, because a deliberate service stop is expected to remain stopped.

Verify readiness after recovery:

```powershell
Invoke-WebRequest 'http://127.0.0.1:8000/api/ready' -UseBasicParsing
```

Expected healthy result: HTTP 200 with `status` equal to `ready`.

## Logging requirement

The installer keeps API stdout/stderr under:

```text
C:\LaSereneHMS\logs\api.stdout.log
C:\LaSereneHMS\logs\api.stderr.log
```

Logs are rotated by NSSM at 10 MiB and/or 24 hours. The same log destinations are retained across API process restarts, so restart evidence is not lost when the process is replaced.

## Failure handling

Do not put database credentials or the production `.env` contents into recovery logs. Recovery failures should direct the operator to the existing service logs and Windows Event Viewer.

## Acceptance for K13.1

K13.1 is accepted when:

- the installer configures automatic restart for unexpected API process exits;
- a normal service stop is not converted into an automatic restart;
- restart delay/throttling is configured;
- persistent stdout/stderr logging remains configured;
- optional PostgreSQL service dependency remains supported;
- no backup, restore, or migration logic is duplicated.

The next K13 subphase will add automated and operator-verifiable database outage/recovery checks around `/api/ready`.
