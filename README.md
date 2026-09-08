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
- Guest and reservation foundation with automatic folio creation
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

## Architecture
The frontend never owns financial/business calculations. Business rules live in the API/domain layer and database writes are transactional.

Authentication is enforced server-side. The frontend stores the short-lived bearer token locally only to maintain the current local session; API permissions are determined by the authenticated user's role.

## Product direction
This system starts with a clean database. Existing Excel files are reference material only; historical spreadsheet data is not imported into the operational database.
