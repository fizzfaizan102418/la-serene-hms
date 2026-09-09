# Phase K11 — Disaster Recovery & Restore Verification

K11 adds a guarded operator workflow around the Phase J PostgreSQL restore machinery. It does not create a second backup/restore implementation.

## Recovery principles

1. Never restore over the live production database.
2. Restore only into a dedicated database whose name ends in `_recovery` or `_recovery_test`.
3. Verify the backup checksum before restore.
4. Require explicit `--confirm` acknowledgement.
5. Validate business date, Alembic revision, and posted financial transaction balance after restore.
6. Treat a recovery database as isolated until validation is complete.

## Windows recovery verification

From an elevated PowerShell prompt on the HMS server or a recovery workstation:

```powershell
C:\LaSereneHMS\scripts\windows\run-recovery-verify.ps1 `
  -BackupFile "C:\LaSereneHMS\backups\la_serene_hms_YYYYMMDDTHHMMSSZ.dump" `
  -ManifestFile "C:\LaSereneHMS\backups\la_serene_hms_YYYYMMDDTHHMMSSZ.manifest.json" `
  -TargetDatabaseUrl "postgresql://recovery_user:<PASSWORD>@127.0.0.1:5432/la_serene_hms_recovery" `
  -Report "C:\LaSereneHMS\backups\recovery-report.json"
```

The target URL is passed only to the running process and is not written to the recovery report. Do not paste real credentials into documentation, PowerShell history, or Git.

The script requires the explicit `--confirm` acknowledgement internally and rejects a target that is not named as an isolated recovery database.

## What is verified

The underlying Phase J restore function first validates the dump manifest, SHA-256 checksum, and file size. It then restores the PostgreSQL custom-format dump with `pg_restore` and verifies:

- exactly one `BusinessDateState` singleton exists;
- the restored business date matches the backup manifest;
- the restored Alembic revision matches the backup manifest;
- every posted financial transaction remains balanced.

A successful recovery command returns `status: verified` and can optionally write an operator report.

## Recovery drill

Perform a recovery drill at least once before production go-live and repeat it after material database/schema changes:

1. Select the newest verified backup.
2. Copy the `.dump` and `.manifest.json` pair to isolated recovery storage.
3. Verify the artifact.
4. Create a clean PostgreSQL recovery database named with `_recovery`.
5. Run the Windows recovery verification script.
6. Confirm `status: verified` and review the recovery report.
7. Check the recovered Alembic revision and business date.
8. Spot-check a guest, reservation, folio, payment, and financial transaction.
9. Record the drill date, backup filename, result, and operator.
10. Do not redirect production traffic until the recovery database has passed operational acceptance.

## Failure handling

If recovery fails:

- keep the source production database untouched;
- preserve the failed recovery logs/report;
- identify whether the failure is checksum, PostgreSQL restore, schema, business-date, or financial-integrity related;
- retry using another known-good verified backup only after the cause is understood;
- do not declare the backup recoverable until a complete verification succeeds.
