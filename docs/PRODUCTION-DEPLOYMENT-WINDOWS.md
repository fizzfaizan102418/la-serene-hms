# Production Deployment — Windows

This is the controlled deployment procedure for the La Serene Hotel production laptop.

## Fixed production layout

```text
Development source: E:\La_Serene_Test_HMS\la-serene-hms
Production install: C:\LaSereneHMS
NSSM:               C:\Tools\nssm\nssm.exe
Service:            LaSereneHMSApi
API:                http://127.0.0.1:8000
```

The deployment scripts are designed for the development and production trees to exist on the same Windows machine.

## Safety contract

The deployment process updates application code but preserves production state:

- `C:\LaSereneHMS\apps\api\.env` is never overwritten.
- `C:\LaSereneHMS\data` is never overwritten.
- PostgreSQL is never deleted or recreated.
- Production virtual environments are preserved.
- Backups and logs are preserved.
- A timestamped application rollback snapshot is created before the update.
- A PostgreSQL backup is created before deployment and before a schema migration.
- Database downgrades/restores are never attempted automatically.
- If deployment fails, the API is left stopped rather than silently running a partially upgraded release.

## Normal release procedure

Open **PowerShell as Administrator** on the production laptop.

### 1. Make sure development is committed

```powershell
cd E:\La_Serene_Test_HMS\la-serene-hms
git status
git pull --ff-only origin main
```

The deployment script refuses to deploy a dirty working tree or a branch other than `main`.

### 2. Run the controlled deployment

```powershell
cd E:\La_Serene_Test_HMS\la-serene-hms
.\scripts\windows\deploy-production.ps1
```

The script will:

1. verify the development and production paths;
2. record the exact Git commit SHA;
3. create a rollback snapshot;
4. create a verified PostgreSQL backup;
5. stop `LaSereneHMSApi` if it was running;
6. copy application code without overwriting production configuration/data;
7. install Python requirements in the production virtual environment;
8. build the React production bundle;
9. run the controlled Alembic upgrade unless `-SkipDatabaseMigration` is supplied;
10. restart `LaSereneHMSApi`;
11. require `/api/ready` HTTP 200;
12. write a release manifest containing the deployed commit and backup locations.

### Deploy code without a migration

Only use this when you have confirmed the release contains no schema changes:

```powershell
.\scripts\windows\deploy-production.ps1 -SkipDatabaseMigration
```

### Deploy without pulling Git

Useful when the development tree has already been updated and tested:

```powershell
.\scripts\windows\deploy-production.ps1 -SkipGitPull
```

## After deployment

Check:

```powershell
.\scripts\windows\check-health.ps1
```

Then open the normal HMS URL and perform a smoke test:

1. login;
2. confirm the expected business date;
3. open Rooms;
4. open Front Desk;
5. open Billing;
6. open Reports;
7. open Night Audit;
8. select a previously closed business date and confirm the historical totals are still present;
9. verify no unexpected errors appear in `C:\LaSereneHMS\logs`.

## Application rollback

If an application deployment fails or the new application is unsafe, stop writes and review the deployment manifest first. Then restore only the application snapshot:

```powershell
.\scripts\windows\rollback-production.ps1
```

Or select a specific snapshot:

```powershell
.\scripts\windows\rollback-production.ps1 -Snapshot "C:\LaSereneHMS\backups\releases\YYYYMMDD-HHMMSS\application"
```

The rollback script preserves `.env`, PostgreSQL data, backups, logs, and the production virtual environment. It does **not** downgrade the database.

If the failed release included a database migration, use the K12/K11 recovery procedure with the verified database backup. Do not run an Alembic downgrade as an emergency guess.

## What not to do

Never use:

```powershell
Remove-Item C:\LaSereneHMS -Recurse -Force
```

Never delete the production PostgreSQL database to deploy a new release.

Never copy a development `.env` over the production `.env`.

Never copy the development `data` directory over production.

Never use `npm run dev` for production.

Never restore a database backup directly over production as a routine rollback.

## Production service

The service is expected to run:

```text
python -m uvicorn app.production:app --host 0.0.0.0 --port 8000
```

NSSM is expected to provide automatic restart for unexpected API process exits while respecting clean operator stops.

## Release evidence

Every deployment creates:

```text
C:\LaSereneHMS\backups\releases\<timestamp>\
    application\
    database\
    database-pre-migration\
    release-manifest.json
```

Keep the release manifest with the operational record. It identifies the deployed Git SHA and the corresponding recovery artifacts.

## Production data policy

The application release is separate from the PostgreSQL data. Deploying new code must not reset reservations, guests, folios, payments, ledger transactions, business dates, or audit history.
