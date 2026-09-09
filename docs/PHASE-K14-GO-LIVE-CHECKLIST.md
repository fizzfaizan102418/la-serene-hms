# Phase K14 — Go-Live Acceptance Record

Use this checklist on the target production Windows host after deployment and before declaring the HMS operationally ready.

## Platform

- [ ] Windows host is approved for production use
- [ ] Production install root exists
- [ ] Required Python environment and application files are present
- [ ] PostgreSQL production service is installed and healthy
- [ ] LAN/firewall access has been validated

## Application

- [ ] Production environment validation passes
- [ ] API Windows service exists and is Running
- [ ] API service recovery policy has been verified under K13
- [ ] React production build is present and served by FastAPI
- [ ] `/api/ready` returns HTTP 200
- [ ] Login succeeds
- [ ] Critical front-desk smoke test succeeds

## Database

- [ ] PostgreSQL is the production database
- [ ] Alembic reports one application head
- [ ] No pending/divergent production migrations exist
- [ ] Business date is correct

## Backup and recovery

- [ ] Scheduled PostgreSQL backup task exists
- [ ] Latest backup exists
- [ ] Latest backup manifest/checksum verifies using Phase J
- [ ] K11 isolated recovery verification has been completed
- [ ] Recovery evidence has been retained

## Operations

- [ ] API stdout/stderr logs exist and are writable
- [ ] Log rotation is configured
- [ ] Backup logs contain no credentials/connection strings
- [ ] Daily operator check is documented
- [ ] Weekly recovery test is scheduled
- [ ] Emergency shutdown/startup procedure is understood
- [ ] Disaster-recovery procedure is understood
- [ ] Release/upgrade checklist is available

## Final decision

Production acceptance should be recorded only after every required item is checked and any exceptions are explicitly documented and approved.

Operator: ____________________

Date/time: ____________________

Release/commit: ____________________

Exceptions: ____________________

Approval: ____________________
