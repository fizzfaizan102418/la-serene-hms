# La Serene HMS backup and disaster recovery

Phase J establishes an operator-controlled PostgreSQL backup and restore-verification workflow.

## Create a backup

Run from the repository root with `HMS_DATABASE_URL` pointing to PostgreSQL:

```bash
python -m ops.backup.backup create --output-dir ./backups --retain 7
```

The command refuses non-PostgreSQL databases and refuses to run when the persisted `BusinessDateState` singleton is missing or when the database has more than one Alembic revision row. The dump is PostgreSQL custom format, which is suitable for `pg_restore`.

Each backup produces a `.dump` file and a `.manifest.json` containing the SHA-256 checksum, file size, persisted business date, and Alembic revision. The dump is written to a temporary directory and moved into place only after `pg_dump` completes; the manifest is written atomically afterward.

## Verify a backup artifact

```bash
python -m ops.backup.backup verify \
  ./backups/la_serene_hms_YYYYMMDDTHHMMSSZ.dump \
  ./backups/la_serene_hms_YYYYMMDDTHHMMSSZ.manifest.json
```

Verification checks the filename, SHA-256 checksum, and byte count before a restore is attempted.

## Restore verification

Restore into a dedicated PostgreSQL recovery database, never into the live production database:

```bash
python -m ops.backup.backup restore-verify \
  ./backups/la_serene_hms_YYYYMMDDTHHMMSSZ.dump \
  ./backups/la_serene_hms_YYYYMMDDTHHMMSSZ.manifest.json \
  --target-database-url postgresql://hms:hms@127.0.0.1:5432/la_serene_hms_recovery
```

The verification is non-destructive to the source database. After restore it checks the BusinessDateState singleton, matches the persisted business date and Alembic revision to the manifest, and rejects any unbalanced posted financial transaction.

## Operational recovery rule

A recovery database must be isolated from production. Validate the restored schema and financial integrity before redirecting application traffic. Backups must be copied to storage independent of the production machine so a local disk failure cannot destroy both the live database and its recovery artifacts.
