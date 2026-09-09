[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$PythonExe = "",
    [string]$BackupDir = "",
    [int]$Retain = 7
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
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$databaseLine = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match '^\s*HMS_DATABASE_URL\s*=' } | Select-Object -First 1
if (-not $databaseLine) { throw "HMS_DATABASE_URL is missing from $EnvFile" }
$env:HMS_DATABASE_URL = ($databaseLine -split '=', 2)[1].Trim().Trim('"').Trim("'")
if (-not $env:HMS_DATABASE_URL.StartsWith('postgresql://') -and -not $env:HMS_DATABASE_URL.StartsWith('postgresql+psycopg://')) {
    throw "HMS_DATABASE_URL must use PostgreSQL for production backups"
}

$env:PYTHONPATH = $InstallRoot
Push-Location $InstallRoot
try {
    & $PythonExe -m ops.backup.scheduled --output-dir $BackupDir --retain $Retain
    if ($LASTEXITCODE -ne 0) {
        throw "Scheduled HMS backup failed. Inspect $BackupDir\backup.log."
    }
} finally {
    Remove-Item Env:HMS_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Pop-Location
}
