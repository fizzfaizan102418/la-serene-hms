# Phase 1 Foundation

## Principles
1. Offline-first: hotel operations continue without internet.
2. One source of truth: calculations are performed in the backend/domain layer.
3. Transactional financial operations: folio, payments, revenue and room state changes commit atomically.
4. Auditability: operational and financial mutations will be logged.
5. Backups: local automatic backups will be added before production use.

## First domain tables
Users, roles, rooms, room_types, guests, reservations, reservation_rooms, folios, folio_items, payments, expenses, audit_logs.

## Data policy
The application starts with a clean operational database. Existing spreadsheets are reference material for designing workflows and validation rules, not a source for historical-data migration.
