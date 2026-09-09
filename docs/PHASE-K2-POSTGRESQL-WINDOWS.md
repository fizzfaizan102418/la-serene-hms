# Phase K2 — Windows PostgreSQL Installation and Bootstrap

## Purpose

Prepare a Windows production machine with PostgreSQL for La Serene HMS. This phase establishes the database service and application database/user. Alembic migrations are intentionally handled by Phase K3.

## Prerequisites

- Windows 10/11 or Windows Server supported by the PostgreSQL release selected for deployment.
- Administrator access to install and manage Windows services.
- PostgreSQL installed locally.
- PostgreSQL client tools available, including `psql.exe`.
- HMS source code checked out on the production machine.
- A strong, unique application database password (20+ characters recommended).

The repository's API already includes the PostgreSQL psycopg driver, so no additional Python database driver is required for K2.

## 1. Install PostgreSQL

Install a supported PostgreSQL Windows release using the official PostgreSQL Windows installer. During installation:

1. Keep the PostgreSQL service enabled for automatic startup.
2. Record the PostgreSQL administrator (`postgres`) password securely.
3. Keep the default PostgreSQL port `5432` unless the deployment requires another port.
4. Do not commit either administrator or application passwords to the repository.

The bootstrap script defaults to PostgreSQL 17 at:

```text
C:\Program Files\PostgreSQL\17\bin
```

If another supported version is installed, pass its `bin` directory with `-PgBin`.

## 2. Verify the PostgreSQL service

From an elevated PowerShell session:

```powershell
Get-Service *postgres* | Select-Object Name, Status, StartType
```

The PostgreSQL service should be `Running` and configured for automatic startup.

If it is stopped, start the PostgreSQL service using Windows Services or the service-management command appropriate to the installed service name.

## 3. Bootstrap the HMS database

From the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\bootstrap-postgresql.ps1
```

Optional parameters:

```powershell
.\scripts\windows\bootstrap-postgresql.ps1 `
  -PgBin "C:\Program Files\PostgreSQL\17\bin" `
  -Host "127.0.0.1" `
  -Port 5432 `
  -Database "la_serene_hms" `
  -AppUser "la_serene_hms_app"
```

The script:

- verifies that `psql.exe` exists;
- validates the database and role identifiers;
- verifies PostgreSQL connectivity;
- creates or updates the dedicated HMS application role;
- creates the HMS database if needed;
- makes the application role the database owner;
- removes PUBLIC database privileges and grants the application role CONNECT;
- never writes the real credentials to a repository file.

The script asks for the administrator and application passwords interactively. The application password is passed only during the bootstrap operation and must not be copied into Git, screenshots, tickets, or documentation.

## 4. Configure production environment variables

Create the production environment configuration outside Git. The API expects the `HMS_` prefix.

Required production values:

```text
HMS_ENVIRONMENT=production
HMS_DATABASE_URL=postgresql+psycopg://la_serene_hms_app:<URL_ENCODED_PASSWORD>@127.0.0.1:5432/la_serene_hms
HMS_SECRET_KEY=<STRONG_RANDOM_SECRET>
HMS_TOKEN_EXPIRE_MINUTES=480
```

The password must be URL-encoded when it contains characters with special meaning in a PostgreSQL URL.

Do not put real production values in `.env.example`, source control, or CI logs.

## 5. Verify database access

Using the application credentials, verify the database endpoint before moving to K3. For example, use `psql` with the application user and confirm that it can connect to `la_serene_hms`.

The application user should be able to connect to the HMS database but should not be used as the PostgreSQL cluster administrator.

## 6. What K2 does not do

K2 does **not** run `alembic upgrade head`. Schema initialization and migration execution belong to K3 so that production database creation and schema migration remain separate, testable operations.

K2 also does not configure the FastAPI Windows service, firewall rules, frontend hosting, or automated backups. Those are later Phase K stages.

## Acceptance checklist

- [ ] PostgreSQL installed on the Windows production host.
- [ ] PostgreSQL service starts automatically.
- [ ] `psql.exe` is available.
- [ ] Dedicated HMS application role exists.
- [ ] `la_serene_hms` database exists and is owned by the application role.
- [ ] PUBLIC database access has been revoked.
- [ ] Application credentials connect successfully.
- [ ] `HMS_DATABASE_URL` is configured outside Git.
- [ ] `HMS_SECRET_KEY` is configured outside Git.
- [ ] No production secret was committed.
- [ ] Alembic migrations have **not** been mixed into this bootstrap step; K3 follows.
