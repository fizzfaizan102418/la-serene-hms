# La Serene HMS — Production End-to-End QA Checklist

This checklist is for the hotel laptop at `D:\LaSereneHMS`. It is deliberately focused on real operational workflows rather than synthetic unit-only checks.

## Automated validation already completed
- API test suite: **165 tests passed, 6 skipped**.
- Frontend production build: **passed**.
- Guest search limit tests: **passed**.
- Duplicate guest create/update protection tests: **passed**.
- Full automated validation was run before the hardened commit was pushed.

## 1. Pre-flight
- Pull the exact GitHub `main` commit.
- Build the web app with `npm.cmd run build`.
- Restart `LaSereneHMSApi` after API changes.
- Confirm `/api/ready` returns HTTP 200.
- Confirm the latest PostgreSQL `.dump` backup exists before testing.

## 2. Guest master / search
- Search by guest name, phone and email.
- Verify the API never returns more than 20 default guest-search results.
- Create a new guest with a unique phone/ID.
- Attempt a duplicate phone or ID and verify the API returns HTTP 409 with matching guest information.
- Edit a guest and verify the duplicate protection excludes the guest being edited.

## 3. Reservation lifecycle
- Create a reservation for one room.
- Create a multi-room reservation.
- Verify active reservations are visible and checked-out/cancelled history is hidden by default.
- Search historical reservations and confirm they remain discoverable.
- Check in, add room/service charges, and verify the folio.

## 4. Payments and folio integrity
- Record cash, card and bank-transfer payments on controlled test reservations.
- Verify outstanding balance changes exactly by the payment amount.
- Verify overpayment/invalid amounts are rejected.
- Correct/reverse a charge and confirm the financial ledger remains balanced.
- Close only when the authoritative folio total matches the financial ledger.

## 5. Expenses and physical cash
- Post a cash expense and verify physical cash decreases by exactly that amount.
- Post a bank/card expense and verify physical cash does not change.
- Void an expense and verify the reversal is reflected without deleting audit history.

## 6. Consecutive business dates / night audit
- Close a controlled business date with no open operational blockers.
- Confirm the next business date opens automatically.
- Before closing the next date, verify opening cash equals the prior day's physical closing cash.
- Verify current cash receipts and current cash expenses affect only the current day's cash.
- Verify trial balance, revenue reconciliation, payment reconciliation and cash difference are balanced.
- Repeat for at least two consecutive business-date transitions.

## 7. Backup / restore
- Create a fresh PostgreSQL `.dump` from the Backup screen.
- Verify the backup is listed and downloadable.
- Perform restore only against a safe/test database or controlled maintenance window.
- Verify the application comes back healthy and key guest/reservation/folio records remain present.
- Verify a pre-restore safety backup was created.

## 8. Role permissions
- `admin`: full configuration, financial, backup/restore and user-authorized actions.
- `reception`: guest/reservation/front-desk operational actions but no admin-only configuration/restore.
- `housekeeping`: room/housekeeping status work without financial/admin actions.
- For every forbidden action, verify both the UI and API reject it; do not rely on hidden buttons alone.

## 9. Current known real-world reconciliation case
The historical 2026-09-15 Excel reconciliation must remain consistent:
- Opening cash: PKR 45,176
- Cash receipts: PKR 0
- Expenses: PKR 28,950
- Closing cash: PKR 16,226
- Revenue: PKR 36,000
- Pending/company receivable: PKR 36,000

For the following business date, the carry-forward check previously established is:
- Opening cash: PKR 16,226
- Cash received: PKR 40,500
- Expenses: PKR 0
- Projected closing cash: PKR 56,726

## Pass criteria
Any financial mismatch, unexpected duplicate guest creation, unauthorized API mutation, failed backup validation, or broken business-date carry-forward is a release blocker.
