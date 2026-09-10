[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$PythonExe = "",
    [string]$NssmExe = "",
    [string]$ServiceName = "LaSereneHMSApi",
    [string]$ServiceDisplayName = "La Serene HMS API",
    [string]$ListenHost = "0.0.0.0",
    [int]$ListenPort = 8000,
    [string]$PostgresServiceName = ""
)

$ErrorActionPreference = "Stop"

function Require-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label not found: $Path"
    }
}

$ApiRoot = Join-Path $InstallRoot "apps\api"
$EnvFile = Join-Path $ApiRoot ".env"
$LogRoot = Join-Path $InstallRoot "logs"

if (-not (Test-Path -LiteralPath $ApiRoot -PathType Container)) {
    throw "API directory not found: $ApiRoot"
}
Require-File $EnvFile "Production environment file"

if (-not $PythonExe) {
    $PythonExe = Join-Path $InstallRoot ".venv\Scripts\python.exe"
}
Require-File $PythonExe "Python executable"

if (-not $NssmExe) {
    $NssmExe = (Get-Command nssm.exe -ErrorAction SilentlyContinue).Source
}
if (-not $NssmExe) {
    throw "nssm.exe was not found. Supply -NssmExe or place NSSM on PATH."
}
Require-File $NssmExe "NSSM executable"

New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

# NSSM writes a diagnostic message to stderr when the service does not yet exist.
# Treat that expected case as a normal install path without weakening error handling
# for the actual service installation/configuration commands below.
$existing = $false
$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    & $NssmExe status $ServiceName 2>$null
    $existing = $LASTEXITCODE -eq 0
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}

if ($existing) {
    & $NssmExe stop $ServiceName 2>$null
    & $NssmExe remove $ServiceName confirm
}

& $NssmExe install $ServiceName $PythonExe "-m uvicorn app.production:app --host $ListenHost --port $ListenPort"
& $NssmExe set $ServiceName DisplayName $ServiceDisplayName
& $NssmExe set $ServiceName Description "La Serene HMS FastAPI production service"
& $NssmExe set $ServiceName AppDirectory $ApiRoot
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

# K13 service-recovery contract:
# - A clean NSSM/service stop must stay stopped.
# - An unexpected API process exit must restart automatically.
# - Restart attempts are throttled to avoid a tight crash loop.
& $NssmExe set $ServiceName AppExit 0 Exit
& $NssmExe set $ServiceName AppExit Default Restart
& $NssmExe set $ServiceName AppRestartDelay 5000
& $NssmExe set $ServiceName AppThrottle 5000
& $NssmExe set $ServiceName AppStopMethodConsole 1500
& $NssmExe set $ServiceName AppStopMethodWindow 1500
& $NssmExe set $ServiceName AppStopMethodThreads 1500
& $NssmExe set $ServiceName AppNoConsole 1
& $NssmExe set $ServiceName AppStdout (Join-Path $LogRoot "api.stdout.log")
& $NssmExe set $ServiceName AppStderr (Join-Path $LogRoot "api.stderr.log")
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateOnline 1
& $NssmExe set $ServiceName AppRotateBytes 10485760
& $NssmExe set $ServiceName AppRotateSeconds 86400

if ($PostgresServiceName) {
    $service = Get-Service -Name $PostgresServiceName -ErrorAction SilentlyContinue
    if ($service) {
        & $NssmExe set $ServiceName DependOnService $PostgresServiceName
    } else {
        Write-Warning "PostgreSQL service '$PostgresServiceName' was not found; continuing without a dependency."
    }
}

& $NssmExe start $ServiceName
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start $ServiceName. Inspect $LogRoot\api.stderr.log and Windows Event Viewer."
}

Write-Host "Installed and started $ServiceDisplayName ($ServiceName)."
Write-Host "API listener: http://$ListenHost`:$ListenPort"
Write-Host "Unexpected process exits are configured for automatic restart."
Write-Host "API logs: $LogRoot"
Write-Host "Production secrets remain in $EnvFile and are not copied into the service script."
