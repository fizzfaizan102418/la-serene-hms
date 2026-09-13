# La Serene HMS — Final Hotel Go-Live Checklist

## 1. Before deployment
- [ ] Confirm the release is merged to `main` and all CI is green.
- [ ] Confirm the DEV workspace is clean and passes the full release gate.
- [ ] For any existing installation being upgraded, create and verify a PostgreSQL dump before changing it.
- [ ] For the first hotel installation, provision a fresh PostgreSQL database and application role.
- [ ] Create the production `.env` on the hotel laptop; never commit or copy it from DEV.

## 2. Technician-only hotel installation
Run the technician installer from the release's `scripts\windows` directory:

```powershell
.\install-hotel.ps1 -InstallRoot C:\LaSereneHMS -NssmExe C:\Tools\nssm\nssm.exe -PostgresServiceName postgresql-x64-18 -RunAsSystem
```

The installer expects the production Python environment at:
`C:\LaSereneHMS\apps\api\.venv\Scripts\python.exe`

It installs the API Windows service, registers the PostgreSQL backup task, and runs the read-only go-live validator. Database provisioning, application build, migrations, and production `.env` creation remain controlled technician steps.

## 3. Required installation sequence
1. Install PostgreSQL.
2. Provision the fresh hotel database/application role with `bootstrap-postgresql.ps1`.
3. Place the release under `C:\LaSereneHMS`.
4. Create `C:\LaSereneHMS\apps\api\.venv` and install API dependencies.
5. Create the production `.env` with the hotel PostgreSQL URL and secrets.
6. Build the React production bundle.
7. Run the controlled Alembic migration.
8. Run `install-hotel.ps1`.
9. Verify `/api/ready` returns HTTP 200.
10. Bootstrap the first administrator account.
11. Create a desktop/browser shortcut for staff.
12. Complete the hotel acceptance smoke test.

## 4. Hotel acceptance smoke test
- [ ] Login succeeds.
- [ ] Dashboard opens.
- [ ] Create a room and verify room status.
- [ ] Create a guest.
- [ ] Create a reservation.
- [ ] Check in the guest.
- [ ] Run Night Audit and verify room-charge accrual.
- [ ] Verify folio and payment.
- [ ] Check out with zero balance.
- [ ] Verify the room becomes dirty after checkout.
- [ ] Verify reports remain reproducible for the selected business date.
- [ ] Verify posted billing items are immutable and corrections use the reversal/correction workflow.
- [ ] Verify Expenses if it is part of day-one hotel scope.
- [ ] Run a PostgreSQL backup and confirm a `.dump` file exists.
- [ ] Confirm there are no development/test records in the hotel database.

## 5. Staff operation
Hotel staff should only need to:
- open the La Serene HMS shortcut/browser page;
- log in;
- operate the UI.

Staff should not need Python, PowerShell, Git, PostgreSQL, or any terminal commands.

## 6. Recovery
- Keep recent verified PostgreSQL dumps available to the technician/admin.
- Do not automatically downgrade or restore a production database after a failed deployment.
- Use the application rollback snapshot and the documented PostgreSQL restore procedure only after reviewing the failure and selecting the correct backup.
