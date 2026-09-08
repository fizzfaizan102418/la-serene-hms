-- PMS Core 2.0 foundation: groups, room-level stays, and multi-window folios.
-- Designed to be safe for existing SQLite installations.

CREATE TABLE IF NOT EXISTS booking_groups (
    id INTEGER PRIMARY KEY,
    code VARCHAR(40) NOT NULL UNIQUE,
    name VARCHAR(160) NOT NULL,
    group_type VARCHAR(40) NOT NULL DEFAULT 'group',
    primary_guest_id INTEGER,
    company_name VARCHAR(160),
    contact_phone VARCHAR(40),
    contact_email VARCHAR(160),
    check_in DATE,
    check_out DATE,
    status VARCHAR(30) NOT NULL DEFAULT 'tentative',
    notes TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (primary_guest_id) REFERENCES guests(id)
);

CREATE INDEX IF NOT EXISTS idx_booking_groups_dates ON booking_groups(check_in, check_out);
CREATE INDEX IF NOT EXISTS idx_booking_groups_status ON booking_groups(status);

CREATE TABLE IF NOT EXISTS group_reservations (
    group_id INTEGER NOT NULL,
    reservation_id INTEGER NOT NULL,
    role VARCHAR(30) NOT NULL DEFAULT 'member',
    notes TEXT,
    PRIMARY KEY (group_id, reservation_id),
    FOREIGN KEY (group_id) REFERENCES booking_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (reservation_id) REFERENCES reservations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_group_reservations_reservation ON group_reservations(reservation_id);

CREATE TABLE IF NOT EXISTS stays (
    id INTEGER PRIMARY KEY,
    reservation_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    guest_id INTEGER,
    status VARCHAR(30) NOT NULL DEFAULT 'reserved',
    check_in DATE NOT NULL,
    check_out DATE NOT NULL,
    actual_check_in DATETIME,
    actual_check_out DATETIME,
    agreed_rate NUMERIC(12,2) NOT NULL DEFAULT 0,
    discount_percent NUMERIC(5,2) NOT NULL DEFAULT 0,
    discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    payment_due_policy VARCHAR(30) NOT NULL DEFAULT 'at_checkout',
    deposit_required NUMERIC(12,2) NOT NULL DEFAULT 0,
    deposit_received NUMERIC(12,2) NOT NULL DEFAULT 0,
    notes TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (reservation_id) REFERENCES reservations(id) ON DELETE CASCADE,
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (guest_id) REFERENCES guests(id)
);

CREATE INDEX IF NOT EXISTS idx_stays_reservation ON stays(reservation_id);
CREATE INDEX IF NOT EXISTS idx_stays_room_dates ON stays(room_id, check_in, check_out);
CREATE INDEX IF NOT EXISTS idx_stays_status ON stays(status);

CREATE TABLE IF NOT EXISTS folio_windows (
    id INTEGER PRIMARY KEY,
    folio_id INTEGER NOT NULL,
    name VARCHAR(80) NOT NULL,
    payer_type VARCHAR(30) NOT NULL DEFAULT 'guest',
    guest_id INTEGER,
    group_id INTEGER,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (folio_id) REFERENCES folios(id) ON DELETE CASCADE,
    FOREIGN KEY (guest_id) REFERENCES guests(id),
    FOREIGN KEY (group_id) REFERENCES booking_groups(id)
);

CREATE INDEX IF NOT EXISTS idx_folio_windows_folio ON folio_windows(folio_id);
CREATE INDEX IF NOT EXISTS idx_folio_windows_group ON folio_windows(group_id);

-- Backward-compatible defaults for existing folios are created lazily by the API.
