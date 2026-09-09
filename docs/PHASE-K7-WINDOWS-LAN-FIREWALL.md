# Phase K7 — Windows LAN Networking and Firewall

## Network model

The standard production deployment is one Windows HMS server on the hotel private LAN:

```text
Hotel PCs/tablets
       |
       | HTTP :8000
       v
Windows HMS Server
  ├── FastAPI + React
  └── PostgreSQL (localhost)
```

The API listens on `0.0.0.0:8000` so other hotel devices can reach it. PostgreSQL remains local to the server and is not opened to the LAN.

## Configure Windows Firewall

Run PowerShell as Administrator on the HMS server:

```powershell
.\scripts\windows\configure-firewall.ps1
```

The rule permits TCP/8000 only on Domain/Private Windows firewall profiles and only from `LocalSubnet`.

Do not create a public-profile rule or expose PostgreSQL port 5432 to the hotel LAN.

## Verify locally

```powershell
Test-NetConnection 127.0.0.1 -Port 8000
Invoke-WebRequest http://127.0.0.1:8000/api/health
```

## Verify from another hotel PC

Find the server LAN address:

```powershell
ipconfig
```

Then from a hotel workstation:

```powershell
Test-NetConnection SERVER-LAN-IP -Port 8000
```

Open:

```text
http://SERVER-LAN-IP:8000/
```

The HMS login page should load and API requests should remain same-origin.

## Troubleshooting

### Port is not reachable

1. Confirm the service is running:

```powershell
Get-Service LaSereneHMSApi
```

2. Confirm the API is listening:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

3. Confirm the firewall rule:

```powershell
Get-NetFirewallRule -DisplayName "La Serene HMS API"
```

4. Confirm the client and server are on the same trusted LAN/subnet.

### API works locally but not remotely

Check the Uvicorn listener is `0.0.0.0`, not `127.0.0.1`, and verify the Windows network profile is Domain or Private. Do not solve this by opening the firewall to `Any` unless the hotel network architecture has been reviewed.

## Security boundary

This phase provides LAN connectivity, not public internet security. Do not port-forward TCP/8000 from the hotel router to the public internet. Internet-facing deployment requires HTTPS and a reverse proxy/security review.
