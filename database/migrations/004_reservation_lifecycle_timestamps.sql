-- Preserve actual front-desk lifecycle timestamps for historical reporting.
-- SQLite supports adding nullable columns without rewriting existing rows.

ALTER TABLE reservations ADD COLUMN checked_in_at DATETIME;
ALTER TABLE reservations ADD COLUMN checked_out_at DATETIME;

CREATE INDEX IF NOT EXISTS idx_reservations_checked_in_at ON reservations(checked_in_at);
CREATE INDEX IF NOT EXISTS idx_reservations_checked_out_at ON reservations(checked_out_at);
