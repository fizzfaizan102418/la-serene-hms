# La Serene HMS — Backup and Disaster Recovery

## Current verified baseline

The production PostgreSQL backup workflow has been validated with an independent restore into a disposable PostgreSQL database. The verified backup records the backup filename, SHA-256 checksum, business date, and Alembic revision.

## Required production policy

A local backup is not sufficient for disaster recovery. The hotel deployment should maintain at least one **independent/off-site copy** that is not stored on the same laptop or physical disk as the live PostgreSQL database.

Recommended minimum policy:

1. Run the existing verified PostgreSQL backup at least daily, preferably after the hotel business day closes.
2. Retain multiple recent local backups rather than relying on a single dump.
3. Copy each verified dump and its manifest to an independent destination (for example, an approved cloud drive, NAS at another location, or removable media stored off-site).
4. Keep the SHA-256 manifest with the dump so the copied file can be integrity-checked before recovery.
5. Protect the off-site destination with an account/password that is separate from the hotel laptop's Windows account where practical.
6. Test an independent restore periodically. A backup is considered operationally verified only when a restore succeeds and the expected Alembic revision/business date can be read from the recovered database.
7. Never use the live hotel database as the target of a recovery drill. Restore into a disposable PostgreSQL database or isolated recovery machine.

## Recovery priorities

### 1. Laptop/application failure

Install the same La Serene HMS revision, provision PostgreSQL, restore the latest verified dump, apply migrations only when required, and verify the application health endpoint and browser login before returning the system to hotel use.

### 2. Local disk failure

Recover the latest verified off-site dump onto a replacement machine. Do not rely on backups stored only under the failed installation directory.

### 3. Corrupt or unusable latest backup

Use the previous verified dump and record the recovery point explicitly. Do not overwrite the only known-good backup while troubleshooting a failed restore.

## Operational acceptance checklist

- [x] PostgreSQL dump creation verified.
- [x] Backup manifest and SHA-256 checksum generated.
- [x] Independent disposable-database restore verified.
- [x] Restored business date and Alembic revision verified.
- [ ] Off-site copy destination selected and access-controlled.
- [ ] Automated or scheduled off-site copy configured on the hotel deployment.
- [ ] Periodic independent restore drill scheduled.

Cloud/Head Office synchronization is a separate future architecture decision and is not required for the current local hotel deployment. The off-site backup requirement remains applicable even while the application is offline-first.
