# Phase B.2 concurrency hardening

Financial posting that consumes a finite authoritative balance must serialize on the relevant parent record before re-checking that balance.

The current implementation uses PostgreSQL row locks for folio payments, payment refunds, and deposit application/refund posting. Idempotency remains backed by the existing `financial_transactions.idempotency_key` infrastructure.

Balance-critical finance reports and deposit ledger responses read balances from posted ledger entries; operational rows remain available for event/detail display only.
