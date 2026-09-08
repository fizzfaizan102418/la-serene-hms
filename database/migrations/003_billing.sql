-- Billing / folio integrity indexes.
-- The application calculates totals from FolioItem and Payment rows;
-- no monetary total is duplicated into the database.

CREATE INDEX IF NOT EXISTS idx_folio_items_folio_id ON folio_items(folio_id);
CREATE INDEX IF NOT EXISTS idx_payments_folio_id ON payments(folio_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_folios_reservation_id ON folios(reservation_id);
