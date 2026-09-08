# La Serene HMS

Offline-first Hotel Management System (PMS/HMS) for La Serene Hotel.

## Phase 1 — Foundation
- FastAPI backend
- React + TypeScript frontend
- SQLite local database
- Initial dashboard shell
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

### Web
```bash
cd apps/web
npm install
npm run dev
```

## Architecture
The frontend never owns financial/business calculations. Business rules live in the API/domain layer and database writes are transactional.

## Product direction
This system starts with a clean database. Existing Excel files are reference material only; historical spreadsheet data is not imported into the operational database.
