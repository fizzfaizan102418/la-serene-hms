# Phase K14 — Production Lifecycle & Go-Live Operations

## Objective

Provide one controlled operator lifecycle for the production HMS after K13 service resilience is in place.

K14 is operational documentation and verification. It does not introduce a second backup, restore, migration, or service-recovery implementation.

## Daily operator checklist

1. Confirm `LaSereneHMSApi` is Running.
2. Check `http://127.0.0.1:8000/api/ready` returns HTTP 200.
3. Confirm the current business date is expected.
4. Confirm the latest scheduled PostgreSQL backup exists and its manifest/checksum verifies.
5. Review recent API and backup logs for errors.
6. Confirm sufficient free disk space for database, logs, and backups.
7. Escalate any failed check before operational use.

## Backup verification

Phase J remains authoritative for backup creation and verification. Operators should verify the latest backup using the existing Phase J tooling rather than creating a parallel mechanism.

Keep verified backups on storage independent from the production machine where practical.

## Weekly recovery test

Use the existing K11 guarded recovery verification against a dedicated recovery database. Never restore a production backup directly over the live production database as a routine test.

Record the backup filename, verification result, recovery target, Alembic revision, and operator/date in the operational record.

## Log maintenance

K13 configures persistent rotating API stdout/stderr logs. Do not delete active logs while the service is running. Archive or remove only rotated/aged logs according to the site's retention policy, preserving enough history for incident investigation.

Backup logs must not contain production credentials or full connection strings.

## Safe application update

1. Schedule a maintenance window.
2. Verify the current `/api/ready` status and latest verified backup.
3. Preserve the current release/build information.
4. Stop the API service cleanly.
5. Deploy the reviewed application build.
6. Validate production environment configuration without printing secrets.
7. Follow K12 for any required database migration.
8. Start the API service.
9. Verify `/api/ready` HTTP 200 and perform a basic login/application smoke test.
10. Review logs for startup errors before declaring the release accepted.

Do not perform an unreviewed migration as part of an application update.

## Database migration procedure

K12 is authoritative. Use the controlled Windows production database upgrade procedure. A pre-upgrade Phase J backup is mandatory, the service must be stopped during migration, and the migration must finish at a single application Alembic head before the service is restarted.

Do not treat Alembic downgrade as an automatic rollback. For a failed or unsafe upgrade, use the verified Phase J/K11 recovery path against an isolated recovery target and follow the incident procedure.

## Emergency shutdown/startup

For an emergency shutdown, stop the API service cleanly first. If database integrity is in question, do not continue application writes; preserve logs and consult the recovery procedure.

For startup, validate the production environment, ensure PostgreSQL is available, start the API service, and require `/api/ready` HTTP 200 before accepting operational traffic.

## Disaster recovery

Phase J owns backup creation/checksum verification and K11 owns guarded restore verification. K14 only coordinates the operator sequence:

1. Identify the latest verified backup.
2. Establish an isolated recovery target.
3. Run K11/Phase J recovery verification.
4. Validate business date, Alembic revision, and financial integrity checks.
5. Only after verification and authorization, proceed with the site's documented production recovery decision.

## Production environment validation

Before go-live or after a machine/application change, validate:

- production PostgreSQL URL is configured and uses PostgreSQL;
- production JWT secret is strong and is not stored in source control;
- required environment variables are present;
- protected `.env` permissions are intact;
- application schema is at the single Alembic head;
- React production build exists;
- API service is configured for automatic recovery;
- persistent logging paths are writable;
- scheduled backup task exists;
- `/api/ready` returns HTTP 200.

Never print the production `.env` or database password during validation.

## Troubleshooting guide

### `/api/ready` returns 503

Check PostgreSQL service status and API stderr logs. Do not run migrations or restore a backup merely because readiness is temporarily unavailable. If PostgreSQL recovers, readiness should return 200 without schema changes.

### API service is stopped

Check Windows service status and persistent API logs. Start the service only after confirming PostgreSQL and production environment prerequisites.

### API repeatedly restarts

Treat repeated restarts as an incident. Inspect API stderr/stdout and Windows service state. Do not disable the recovery policy merely to hide the symptom.

### Backup task fails

Check the scheduled-task history and backup log. Do not copy credentials into tickets or logs. Run the existing Phase J backup command manually during a controlled maintenance window if required.

### Migration fails

Leave the API stopped. Preserve logs and the pre-upgrade backup. Do not guess with downgrade commands. Follow K12 and the Phase J/K11 recovery process.

## Release / upgrade checklist

- [ ] Maintenance window approved
- [ ] Verified pre-release backup exists
- [ ] Release/build identified
- [ ] Production environment validated
- [ ] API stopped before migration when migration is required
- [ ] K12 migration procedure completed
- [ ] API restarted
- [ ] `/api/ready` HTTP 200
- [ ] Login and critical application smoke test passed
- [ ] Logs reviewed
- [ ] Backup task still configured
- [ ] Release accepted and rollback/recovery evidence retained

## Final go-live checklist

- [ ] PostgreSQL production database healthy
- [ ] Alembic at one application head
- [ ] Production secrets validated and protected
- [ ] React production build served by FastAPI
- [ ] API Windows service installed and recovery policy verified
- [ ] `/api/ready` HTTP 200
- [ ] Scheduled backup task installed
- [ ] Latest backup checksum verified
- [ ] Dedicated recovery test completed according to K11
- [ ] Logs persist and rotate
- [ ] Firewall/LAN access validated
- [ ] Daily operator checklist assigned
- [ ] Weekly recovery-test schedule established
- [ ] Emergency shutdown/startup procedure reviewed
- [ ] Disaster-recovery procedure reviewed
- [ ] Final acceptance record completed

## Ownership boundaries

| Responsibility | Authoritative phase |
|---|---|
| Business date / PMS foundation | Phase A |
| Financial ledger integrity | Phase B |
| Front desk transactional integrity | Phase C |
| Night audit | Phase D |
| Restaurant / POS | Phase E |
| Inventory | Phase F |
| Purchasing / suppliers | Phase G |
| Housekeeping / maintenance | Phase H |
| Management / reporting | Phase I |
| Backup / restore verification | Phase J |
| Disaster recovery | K11 |
| Database lifecycle / migrations | K12 |
| Service recovery / resilience | K13 |
| Production lifecycle / go-live operations | K14 |

## Acceptance

K14 is accepted when the repository contains the operational runbook, all automated checks remain green, and the production operator completes the final go-live checklist on the target Windows host.