# Phase K6 — Authentication and Secrets

## Authentication model

Production authentication remains bearer-token based:

- users authenticate through `POST /api/auth/login`
- the API signs JWT access tokens with `HMS_SECRET_KEY`
- protected endpoints require `Authorization: Bearer <token>`
- passwords are stored as Argon2 hashes through `pwdlib`
- role checks are enforced server-side

The React application stores the access token in browser local storage. This is acceptable for the current single-server offline/LAN deployment, but it means future external/public deployment should introduce a stronger browser-session design and HTTPS before exposure beyond the trusted hotel network.

## Production secret requirements

The production environment must contain:

```text
HMS_ENVIRONMENT=production
HMS_DATABASE_URL=postgresql+psycopg://...
HMS_SECRET_KEY=<long random secret>
HMS_TOKEN_EXPIRE_MINUTES=480
```

K1 makes the API fail fast if PostgreSQL is missing or the JWT secret is weak/default. The production `.env` file is ignored by Git.

Generate a secret without printing it into repository files:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the generated value manually into the production `.env`.

## Validate and protect the environment file

Run as Administrator:

```powershell
.\scripts\windows\validate-production-env.ps1
.\scripts\windows\protect-production-env.ps1
```

The validator never prints the secret value. The ACL script restricts the secret-bearing file to SYSTEM and local Windows administrators.

## First administrator

On a fresh installation, the UI can create the first administrator through the bootstrap flow. Once any user exists, the API permanently rejects another bootstrap request with HTTP 409.

After creating the administrator, use the normal sign-in flow. Do not reuse the database administrator password as the HMS application password or JWT secret.

## LAN security boundary

The default deployment is intended for the hotel's trusted private LAN. K7 limits the Windows Firewall rule to local/private profiles and the local subnet where practical. Do not expose port 8000 directly to the public internet.

For an internet-facing deployment, HTTPS, stronger session handling, reverse proxying, and a dedicated security review are required.
