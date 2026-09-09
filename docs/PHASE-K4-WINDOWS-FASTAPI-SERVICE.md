# Phase K4 — Windows FastAPI Service

## Purpose

Run the La Serene HMS FastAPI backend as a persistent Windows service. The service uses Uvicorn behind NSSM, starts automatically with Windows, restarts after an unexpected process exit, and writes persistent stdout/stderr logs.

NSSM is a Windows service wrapper; its documented command-line installation and restart behavior are used by the installer. See the official NSSM documentation for the version selected by the hotel administrator.

## Prerequisites

- Windows 10/11 or a supported Windows Server release.
- Python installed and a production virtual environment created at `C:\LaSereneHMS\.venv`, unless another path is supplied.
- Repository/application deployed under `C:\LaSereneHMS`.
- PostgreSQL installed and running.
- `C:\LaSereneHMS\apps\api\.env` populated with production values.
- NSSM available on `PATH` or supplied with `-NssmExe`.
- PowerShell run as Administrator.

## Install

From the repository root:

```powershell
.\scripts\windows\install-api-service.ps1
```

If paths differ:

```powershell
.\scripts\windows\install-api-service.ps1 `
  -InstallRoot 'D:\LaSereneHMS' `
  -PythonExe 'D:\LaSereneHMS\.venv\Scripts\python.exe' `
  -NssmExe 'C:\Tools\nssm\win64\nssm.exe' `
  -PostgresServiceName 'postgresql-x64-17'
```

The installer configures:

- service name: `LaSereneHMSApi`
- automatic Windows startup
- Uvicorn on `0.0.0.0:8000`
- restart after unexpected application exit
- working directory: `apps\api`
- persistent logs under `C:\LaSereneHMS\logs`

The production `.env` is read by the application and is never committed to Git. Do not pass `HMS_SECRET_KEY` or database passwords as command-line arguments.

## Verify

```powershell
Get-Service LaSereneHMSApi
Invoke-WebRequest http://127.0.0.1:8000/api/health
```

Expected health response contains `"status":"ok"`.

From another machine on the hotel LAN, replace the server address with the Windows server's LAN IP:

```text
http://SERVER-LAN-IP:8000/api/health
```

## Restart and recovery

```powershell
Restart-Service LaSereneHMSApi
Get-Service LaSereneHMSApi
```

To verify process recovery, stop the monitored Python process and confirm the service returns to the running state. NSSM is configured to restart the application after an unexpected exit.

After a Windows reboot, confirm:

```powershell
Get-Service LaSereneHMSApi
Invoke-WebRequest http://127.0.0.1:8000/api/health
```

## Logs

- `logs\api.stdout.log`
- `logs\api.stderr.log`
- Windows Event Viewer → Windows Logs → Application

Log files are configured for size/time-based rotation by NSSM. Detailed operational log retention is finalized in K9.

## Remove service

Run as Administrator:

```powershell
nssm stop LaSereneHMSApi
nssm remove LaSereneHMSApi confirm
```

Do not delete the application directory before removing the service.
