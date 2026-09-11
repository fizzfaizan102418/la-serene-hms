[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$BackupRoot = "",
    [string]$ServiceName = "LaSereneHMSApi",
    [string]$NssmExe = "C:\Tools\nssm\nssm.exe",
    [string]$Snapshot = ""
)

$ErrorActionPreference = "Stop"
if (-not $BackupRoot) { $BackupRoot = Join-Path $InstallRoot "backups\releases" }

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -gt 7) { throw "$File failed with robocopy exit code $LASTEXITCODE" }
    if ($LASTEXITCODE -ne 0 -and $File -ne "robocopy") { throw "$File failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path -LiteralPath $InstallRoot -PathType Container)) { throw "Production install root not found: $InstallRoot" }
if (-not (Test-Path -LiteralPath $NssmExe -PathType Leaf)) { throw "NSSM executable not found: $NssmExe" }
if (-not $Snapshot) {
    $manifests = Get-ChildItem -LiteralPath $BackupRoot -Filter "release-manifest.json" -Recurse -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if (-not $manifests) { throw "No release rollback snapshots were found under $BackupRoot" }
    $Snapshot = Join-Path $manifests[0].Directory.FullName "application"
}
if (-not (Test-Path -LiteralPath $Snapshot -PathType Container)) { throw "Rollback application snapshot not found: $Snapshot" }

$ApiEnv = Join-Path $InstallRoot "apps\api\.env"
$DataRoot = Join-Path $InstallRoot "data"
$BackupsRoot = Join-Path $InstallRoot "backups"
$LogsRoot = Join-Path $InstallRoot "logs"

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service -and $service.Status -eq "Running") {
    Write-Host "Stopping $ServiceName..."
    Stop-Service -Name $ServiceName -ErrorAction Stop
    (Get-Service -Name $ServiceName).WaitForStatus("Stopped", "00:00:30")
}

$currentBackup = Join-Path $BackupRoot ("rollback-current-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Force -Path $currentBackup | Out-Null
$currentApp = Join-Path $currentBackup "application"
New-Item -ItemType Directory -Force -Path $currentApp | Out-Null

Write-Host "Saving the current application before rollback: $currentApp"
Invoke-Checked "robocopy" @($InstallRoot, $currentApp, "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/XJ", "/XD", (Join-Path $InstallRoot ".venv"), (Join-Path $InstallRoot "node_modules"), (Join-Path $InstallRoot "apps\web\node_modules"), $DataRoot, $BackupsRoot, $LogsRoot, "/XF", $ApiEnv)

Write-Host "Restoring application snapshot: $Snapshot"
Invoke-Checked "robocopy" @($Snapshot, $InstallRoot, "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/XJ", "/XD", $DataRoot, $BackupsRoot, $LogsRoot, (Join-Path $InstallRoot ".venv"), (Join-Path $InstallRoot "node_modules"), (Join-Path $InstallRoot "apps\web\node_modules"), "/XF", $ApiEnv)

if (-not (Test-Path -LiteralPath $ApiEnv -PathType Leaf)) { throw "Production .env is missing after rollback; refusing to start service." }

if ($service) {
    Write-Host "Starting $ServiceName..."
    Start-Service -Name $ServiceName -ErrorAction Stop
    (Get-Service -Name $ServiceName).WaitForStatus("Running", "00:00:30")
}

try {
    $ready = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/ready" -UseBasicParsing -TimeoutSec 30
    if ($ready.StatusCode -ne 200) { throw "Readiness returned HTTP $($ready.StatusCode)." }
}
catch {
    Write-Warning "Application rollback completed, but readiness is not healthy: $($_.Exception.Message)"
    Write-Warning "Do not restore/downgrade the database automatically. Follow the K11/K12 recovery procedure if the release included schema changes."
    throw
}

Write-Host "APPLICATION ROLLBACK SUCCESSFUL"
Write-Host "Snapshot restored: $Snapshot"
Write-Host "Production .env, PostgreSQL data, backups, logs, and virtual environment were preserved."
Write-Host "Database schema was NOT downgraded."
