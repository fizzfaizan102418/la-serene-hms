# Phase K13.2 — Database Readiness and Recovery

## Purpose

K13.2 makes database outage and recovery observable through the production readiness contract without changing the financial, migration, backup, or disaster-recovery implementations already established in earlier phases.

The production endpoint is:

```text
GET /api/ready
```

Expected states:

| Condition | HTTP status | Meaning |
|---|---:|---|
| PostgreSQL reachable and `SELECT 1` succeeds | 200 | API is ready to serve database-backed requests |
| PostgreSQL unavailable or readiness query fails | 503 | API process may still be running, but the database dependency is not ready |
| PostgreSQL restored and readiness query succeeds again | 200 | Database dependency recovered |

The endpoint intentionally returns a generic failure detail. Database connection strings, usernames, passwords, hostnames, and driver exceptions must never be exposed through the readiness response.

## Scope boundaries

- **K12** remains authoritative for Alembic migration lifecycle and schema safety.
- **Phase J** remains authoritative for PostgreSQL backup creation and verification.
- **K11** remains authoritative for guarded disaster-recovery restore verification.
- **K13.1** remains authoritative for API process restart behavior under NSSM.
- **K13.2** only observes database readiness and verifies the healthy → outage → recovery transition.

Do not perform migrations or restores merely because `/api/ready` reports `503`.

## Windows operator procedure

### 1. Check the PostgreSQL service

Use the PostgreSQL Windows service name installed on the machine. If the name is unknown:

```powershell
Get-Service | Where-Object { $_.DisplayName -like '*PostgreSQL*' }
```

Inspect its state:

```powershell
Get-Service -Name '<PostgreSQLServiceName>'
```

If PostgreSQL is intentionally stopped, start it using the normal Windows service controls:

```powershell
Start-Service -Name '<PostgreSQLServiceName>'
```

Do not restart the database repeatedly if it fails to start. Check PostgreSQL's own logs and Windows Event Viewer.

### 2. Check database readiness

From the production host:

```powershell
try {
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/ready' -UseBasicParsing
    $response.StatusCode
    $response.Content
} catch {
    if ($_.Exception.Response) {
        $_.Exception.Response.StatusCode.value__
    } else {
        throw
    }
}
```

Interpretation:

- `200` means the readiness query succeeded.
- `503` means the API is reachable but the database readiness query failed.
- Connection failure means the API service itself may not be listening; continue with the API service check.

### 3. Check the API service

The K13.1 installer uses service name `LaSereneHMSApi` and display name `La Serene HMS API` by default.

```powershell
Get-Service -Name 'LaSereneHMSApi'
```

If PostgreSQL is healthy but the API is not running, use the normal Windows service recovery operation:

```powershell
Start-Service -Name 'LaSereneHMSApi'
```

For an API process crash, NSSM is configured to restart the process automatically. A clean service stop should remain stopped.

### 4. Safe recovery order

Use this order for a transient outage:

1. Confirm the API service state.
2. Confirm the PostgreSQL service state.
3. Restore/start PostgreSQL if it was unexpectedly unavailable.
4. Wait for PostgreSQL to become operational.
5. Poll `/api/ready` until it returns `200`.
6. If PostgreSQL is healthy but the API is not listening, recover `LaSereneHMSApi`.
7. Re-check `/api/ready` and require `200` before declaring the service recovered.
8. Inspect persistent API logs if recovery does not succeed.

Do not run `alembic upgrade`, restore a backup, or invoke disaster recovery for a simple transient PostgreSQL availability failure unless investigation shows that the database itself is damaged or the schema lifecycle requires intervention.

## Failure and recovery contract

K13.2 tests the readiness state transition as three distinct observations:

```text
healthy PostgreSQL
       |
       v
     HTTP 200
       |
       | PostgreSQL outage
       v
     HTTP 503
       |
       | PostgreSQL restored
       v
     HTTP 200
```

The unit tests simulate the dependency transition without embedding credentials or creating a second recovery mechanism.

## Logging and credential safety

The readiness endpoint returns only the fixed message `Database readiness check failed` on failure. The underlying database exception is retained as the Python exception cause for server-side diagnostics but is not serialized into the API response.

Operators should inspect the existing persistent service logs under the configured K13.1 log directory when deeper diagnosis is required. Never paste production connection strings, passwords, JWT secrets, or `.env` contents into tickets, issues, or logs.

## Acceptance criteria

K13.2 is accepted when:

- healthy PostgreSQL produces `/api/ready` HTTP `200`;
- unavailable PostgreSQL produces `/api/ready` HTTP `503`;
- restored PostgreSQL produces `/api/ready` HTTP `200` again;
- readiness failures expose no database credentials or connection details;
- the production app continues to serve the same readiness response contract;
- CI and Windows PowerShell validation remain green.
