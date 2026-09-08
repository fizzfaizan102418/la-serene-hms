-- Financial Ledger 2.0: trace room folio lines back to the room-level stay that generated them.
-- Existing financial rows remain valid; stay_id is intentionally nullable for historical data.

ALTER TABLE folio_items ADD COLUMN stay_id INTEGER REFERENCES stays(id);
CREATE INDEX IF NOT EXISTS idx_folio_items_stay ON folio_items(stay_id);
