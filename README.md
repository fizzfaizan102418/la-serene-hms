# La Serene HMS

Offline-first Hotel Management System (PMS/HMS) for La Serene Hotel.

## Phase 1 — Foundation
- FastAPI backend
- React + TypeScript frontend
- SQLite local database
- Live operations dashboard
- Authentication with JWT access tokens
- Role-based permissions for admin, reception and housekeeping
- Initial domain schema for users, rooms, guests, reservations, folios, payments, expenses and audit logs
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

## Architecture
The frontend never owns financial/business calculations. Business rules live in the API/domain layer and database writes are transactional.

Authentication is enforced server-side. The frontend stores the short-lived bearer token locally only to maintain the current local session; API permissions are determined by the authenticated user's role.

## Product direction
This system starts with a clean database. Existing Excel files are reference material only; historical spreadsheet data is not imported into the operational database.
