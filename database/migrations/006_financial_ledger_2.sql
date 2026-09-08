-- Financial Ledger 2.0: trace room folio lines back to the room-level stay that generated them.
-- Existing financial rows remain valid; stay_id is intentionally nullable for historical data.

ALTER TABLE folio_items ADD COLUMN stay_id INTEGER REFERENCES stays(id);
CREATE INDEX IF NOT EXISTS idx_folio_items_stay ON folio_items(stay_id);

-- Best-effort backfill for legacy room charges created by the former billing endpoint.
UPDATE folio_items
SET stay_id = (
    SELECT s.id
    FROM stays s
    JOIN folios f ON f.id = folio_items.folio_id
    JOIN rooms r ON r.id = s.room_id
    WHERE s.reservation_id = f.reservation_id
      AND folio_items.category = 'room'
      AND folio_items.description LIKE 'Room ' || r.number || ' ·%'
    LIMIT 1
)
WHERE folio_items.category = 'room'
  AND folio_items.stay_id IS NULL;
