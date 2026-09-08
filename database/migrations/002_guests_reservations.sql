-- Guests & Reservations milestone.
-- The application currently uses SQLAlchemy metadata creation for local SQLite installs.
-- This migration documents the intended operational indexes and constraints for future
-- explicit migration tooling; it is safe to apply after the base tables exist.

CREATE INDEX IF NOT EXISTS ix_guests_phone ON guests(phone);
CREATE INDEX IF NOT EXISTS ix_guests_email ON guests(email);
CREATE INDEX IF NOT EXISTS ix_reservations_check_in ON reservations(check_in);
CREATE INDEX IF NOT EXISTS ix_reservations_check_out ON reservations(check_out);
CREATE INDEX IF NOT EXISTS ix_reservations_guest_id ON reservations(guest_id);
CREATE INDEX IF NOT EXISTS ix_reservation_rooms_room_id ON reservation_rooms(room_id);
