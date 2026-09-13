# La Serene HMS — Final Hotel Go-Live Checklist

## Before deployment
- [ ] Confirm release commit is merged to `main` and CI is green.
- [ ] Confirm DEV workspace is clean and passes the release gate.
- [ ] Take a verified PostgreSQL backup of the existing production-test database before any controlled deployment change.
- [ ] Prepare a fresh PostgreSQL database for the hotel installation.
- [ ] Prepare a production `.env` with the hotel database URL and secret values; never commit it.

## Install on the hotel laptop
1. Copy the release to `C:\LaSereneHMS`.
2. Create the production Python environment under `C:\LaSereneHMS\apps\api\.venv`.
3. Install API dependencies.
4. Build the React production bundle under `C:\LaSereneHMS\apps\web\dist`.
5. Run Alembic migrations against the fresh hotel database.
6. Install `LaSereneHMSApi` as an automatic Windows service.
7. Register the daily PostgreSQL backup task.
8. Configure Windows Firewall only for the required hotel LAN access.
9. Verify `/api/ready` returns HTTP 200.
10. Bootstrap the first administrator account.
11. Open the HMS from the browser/desktop shortcut and verify login.

## Hotel acceptance smoke test
- [ ] Login succeeds.
- [ ] Dashboard opens.
- [ ] Create room and verify room status.
- [ ] Create guest.
- [ ] Create reservation.
- [ ] Check in guest.
- [ ] Run Night Audit and verify room charge accrual.
- [ ] Verify folio and payment.
- [ ] Check out with zero balance.
- [ ] Verify room becomes dirty after checkout.
- [ ] Verify reports use the selected business date.
- [ ] Verify posted billing items are immutable and corrections use reversal/correction workflow.
- [ ] Verify expense posting, void, and reporting if Expenses is in day-one scope.
- [ ] Run a backup and confirm a `.dump` file exists.
- [ ] Confirm the application contains no development/test records.

## Staff operation
Hotel staff should only need to:
- open the La Serene HMS shortcut/browser page;
- log in;
- use the UI.

They should not need Python, PowerShell, Git, or PostgreSQL knowledge.

## Recovery
- Keep the latest verified PostgreSQL dumps available to the technician/admin.
- Do not downgrade a production database automatically after a failed deployment.
- Use the documented rollback procedure and restore only after confirming the correct backup and target database.
