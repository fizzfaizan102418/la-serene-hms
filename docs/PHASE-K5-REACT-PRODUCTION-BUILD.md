# Phase K5 — React Production Build

## Deployment model

La Serene HMS does not require a Vite development server in production. The React application is compiled into `apps/web/dist`, and the production FastAPI entrypoint (`app.production:app`) serves that build from the same origin as the API.

This keeps browser requests such as `/api/auth/login` same-origin and avoids production CORS configuration for the standard single-server deployment.

## Build

On the Windows production machine, from the repository root:

```powershell
.\scripts\windows\build-web.ps1
```

The script runs `npm install` followed by `npm run build` and verifies that `apps\web\dist` exists.

## Start production application

The Windows service must use:

```text
python -m uvicorn app.production:app --host 0.0.0.0 --port 8000
```

Do not use `npm run dev` or `vite` in production.

## Browser access

Open:

```text
http://SERVER-LAN-IP:8000/
```

The React bundle and API are served from the same origin. The Vite development proxy remains available for local development only.

## Rebuild after an application update

1. Stop or restart the HMS API service as required by the deployment procedure.
2. Pull/update the application files.
3. Run `scripts\windows\build-web.ps1`.
4. Run the database migration procedure when the release contains migrations.
5. Start/restart `LaSereneHMSApi`.
6. Open the browser and verify login plus a representative dashboard request.

The complete upgrade sequence is finalized in K13.
