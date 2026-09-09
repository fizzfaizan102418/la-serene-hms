[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$PythonExe = "",
    [string]$BackupDir = "",
    [int]$Retain = 7,
    [string]$ServiceName = "LaSereneHMSApi",
    [switch]$SkipServiceRestart
)

$ErrorActionPreference = "Stop"

function Require-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label not found: $Path"
    }
}

$ApiRoot = Join-Path $InstallRoot "apps\api"
$EnvFile = Join-Path $ApiRoot ".env"
if (-not $PythonExe) { $PythonExe = Join-Path $InstallRoot ".venv\Scripts\python.exe" }
if (-not $BackupDir) { $BackupDir = Join-Path $InstallRoot "backups" }

Require-File $EnvFile "Production environment file"
Require-File $PythonExe "Python executable"
if (-not (Test-Path -LiteralPath $ApiRoot -PathType Container)) {
    throw "API directory not found: $ApiRoot"
}

$databaseLine = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match '^\s*HMS_DATABASE_URL\s*=' } | Select-Object -First 1
if (-not $databaseLine) { throw "HMS_DATABASE_URL is missing from $EnvFile" }
$env:HMS_DATABASE_URL = ($databaseLine -split '=', 2)[1].Trim().Trim('"').Trim("'")
if (-not $env:HMS_DATABASE_URL.StartsWith('postgresql://') -and -not $env:HMS_DATABASE_URL.StartsWith('postgresql+psycopg://')) {
    throw "HMS_DATABASE_URL must use PostgreSQL for production upgrades"
}

$env:PYTHONPATH = $InstallRoot
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$wasRunning = $service -and $service.Status -eq 'Running'

try {
    Write-Host "K12 database lifecycle: creating mandatory pre-upgrade backup..."
    & (Join-Path $InstallRoot "scripts\windows\run-backup.ps1") -InstallRoot $InstallRoot -PythonExe $PythonExe -BackupDir $BackupDir -Retain $Retain
    if ($LASTEXITCODE -ne 0) {
        throw "Pre-upgrade backup failed; migration was not attempted."
    }

    Push-Location $ApiRoot
    try {
        Write-Host "Current Alembic revision before upgrade:"
        & $PythonExe -m alembic current
        if ($LASTEXITCODE -ne 0) { throw "Unable to read current Alembic revision." }

        Write-Host "Application Alembic head(s):"
        & $PythonExe -m alembic heads
        if ($LASTEXITCODE -ne 0) { throw "Unable to read Alembic heads." }
    }
    finally {
        Pop-Location
    }

    if ($wasRunning -and -not $SkipServiceRestart) {
        Write-Host "Stopping $ServiceName for controlled schema upgrade..."
        Stop-Service -Name $ServiceName -ErrorAction Stop
        $service.WaitForStatus('Stopped', '00:00:30')
    }

    Push-Location $ApiRoot
    try {
        Write-Host "Applying Alembic migrations..."
        & $PythonExe -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw "Alembic upgrade failed. Review migration logs and restore from the verified pre-upgrade backup if required."
        }

        Write-Host "Verifying migration state after upgrade:"
        & $PythonExe -m alembic current
        if ($LASTEXITCODE -ne 0) { throw "Unable to read Alembic revision after upgrade." }

        Write-Host "Verifying application schema is exactly at Alembic head..."
        & $PythonExe -c "from app.migration_guard import check_database_at_head; print('Verified revision:', check_database_at_head())"
        if ($LASTEXITCODE -ne 0) { throw "Post-upgrade schema verification failed." }
    }
    finally {
        Pop-Location
    }

    if (-not $SkipServiceRestart -and $service) {
        Write-Host "Starting $ServiceName..."
        Start-Service -Name $ServiceName -ErrorAction Stop
        (Get-Service -Name $ServiceName).WaitForStatus('Running', '00:00:30')

        Write-Host "Checking production readiness..."
        $ready = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/ready" -UseBasicParsing -TimeoutSec 30
        if ($ready.StatusCode -ne 200) {
            throw "Production readiness returned HTTP $($ready.StatusCode)."
        }
        Write-Host "Production database upgrade completed and readiness is healthy."
    } else {
        Write-Host "Migration completed. Service restart was skipped by request."
    }
}
finally {
    Remove-Item Env:HMS_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    if (-not $SkipServiceRestart -and $wasRunning -and $service) {
        $current = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($current -and $current.Status -eq 'Stopped') {
            Write-Warning "$ServiceName remains stopped because the upgrade did not complete successfully."
        }
    }
}
