# La Serene HMS

Offline-first Hotel Management System (PMS/HMS) for La Serene Hotel.

## Phase 1 — Core PMS foundation
- FastAPI backend
- React + TypeScript frontend
- SQLite local database
- Live operations dashboard
- JWT authentication with Argon2 password hashing
- Role-based permissions for admin, reception and housekeeping
- Room types with base rates and descriptions
- Room inventory management
- Operational room statuses: available, reserved, occupied, dirty and out of order
- Visual room map with status filtering and housekeeping controls
- Guest records with search by name, phone or email
- Reservation register with date-aware room availability
- Front desk arrivals, departures and in-house workflow
- Check-in, check-out and room-transfer transactions
- Automatic folio creation for reservations
- Audit logging for important operational mutations
- Offline-first deployment target

## Development

### API
```bash
cd apps/api
python -m venv .venv
# Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Optional environment variables:
- `HMS_SECRET_KEY` — set a strong random secret for signed access tokens.
- `HMS_TOKEN_EXPIRE_MINUTES` — token lifetime in minutes; defaults to `480`.

For local development, the web app uses a Vite proxy to `http://127.0.0.1:8000`.

### Web
```bash
cd apps/web
npm install
npm run dev
```

On a new database, open the web app and create the first administrator account. After initialization, the login screen is used for subsequent sessions.

## Rooms workflow

1. Sign in as an `admin`.
2. Open **Rooms** from the module navigation.
3. Create room types and enter their base rates.
4. Add rooms and assign each room to a room type.
5. Use the room map to filter rooms and update operational status.
6. `reception` and `housekeeping` users may change room status, while room/type setup remains restricted to `admin`.

Room status changes are audited. A reserved room cannot be manually returned to `available` while it is still linked to an active reservation.

## Guests & Reservations workflow

1. Open **Guests** and create a guest record.
2. Search guests by name, phone or email before creating duplicates.
3. Open **Reservations** and enter check-in/check-out dates.
4. Use **Check availability** to retrieve rooms that are operationally usable and do not overlap another active reservation.
5. Select one or more rooms and create the reservation.
6. The API creates the reservation, room links and an open folio in one database transaction and records an audit event.

Availability is date-aware: a room can have a future reservation without being incorrectly treated as unavailable for every other date range. `dirty` and `out_of_order` rooms are excluded from new bookings.

## Front desk workflow

1. Open **Front Desk** to see today's arrivals, departures and current in-house guests.
2. A receptionist or admin can check in an eligible arrival; the reservation becomes `checked_in` and its assigned rooms become `occupied` atomically.
3. A receptionist or admin can check out an in-house reservation; it becomes `checked_out` and occupied rooms move to `dirty` for housekeeping.
4. A checked-in guest can be transferred from an assigned room to an available room. The old room becomes `dirty`, the new room becomes `occupied`, and all reservation/room changes are audited in the same transaction.

The lifecycle deliberately keeps business state transitions in FastAPI rather than in React. The frontend only requests an operation and renders the API result.

## Architecture
The frontend never owns financial/business calculations. Business rules live in the API/domain layer and database writes are transactional.

Authentication is enforced server-side. The frontend stores the short-lived bearer token locally only to maintain the current local session; API permissions are determined by the authenticated user's role.

The intended operational lifecycle is:

```text
Reservation
   ↓
Check-in
   ↓
Room occupied
   ↓
Guest in house
   ↓
Check-out
   ↓
Room dirty
   ↓
Housekeeping
   ↓
Room available
```

Billing, folio finalization, payment capture and daily closing will be added as the next financial milestone. Those calculations will remain server-side and transactional.

## Product direction
This system starts with a clean operational database. Existing Excel files are reference material for workflow and validation design; historical spreadsheet data is not imported into the operational database.
