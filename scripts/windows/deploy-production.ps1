[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [string]$SourceRoot = "E:\La_Serene_Test_HMS\la-serene-hms",
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$NssmExe = "C:\Tools\nssm\nssm.exe",
    [string]$ServiceName = "LaSereneHMSApi",
    [string]$PythonExe = "",
    [string]$BackupRoot = "",
    [switch]$SkipGitPull,
    [switch]$SkipDatabaseMigration
)

$ErrorActionPreference = "Stop"
if (-not $BackupRoot) { $BackupRoot = Join-Path $InstallRoot "backups\releases" }
if (-not $PythonExe) { $PythonExe = Join-Path $InstallRoot "apps\api\.venv\Scripts\python.exe" }

function Require-Path([string]$Path, [string]$Label, [bool]$Directory = $true) {
    $kind = if ($Directory) { 'Container' } else { 'Leaf' }
    if (-not (Test-Path -LiteralPath $Path -PathType $kind)) { throw "$Label not found: $Path" }
}

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    $exitCode = $LASTEXITCODE
    if ($File -ieq "robocopy") {
        if ($exitCode -gt 7) { throw "$File failed with exit code $exitCode" }
    }
    elseif ($exitCode -ne 0) {
        throw "$File failed with exit code $exitCode"
    }
}

Require-Path $SourceRoot "Development source root"
Require-Path (Join-Path $SourceRoot ".git") "Git repository"
Require-Path $InstallRoot "Production install root"
Require-Path $NssmExe "NSSM executable" $false
Require-Path $PythonExe "Production Python executable" $false

$ApiRoot = Join-Path $InstallRoot "apps\api"
$WebRoot = Join-Path $InstallRoot "apps\web"
$EnvFile = Join-Path $ApiRoot ".env"
Require-Path $EnvFile "Production environment file" $false

if (-not $SkipGitPull) {
    Push-Location $SourceRoot
    try {
        Write-Host "Updating development source from origin/main..."
        Invoke-Checked "git" @("pull", "--ff-only", "origin", "main")
    }
    finally { Pop-Location }
}

Push-Location $SourceRoot
try {
    $branch = ((& git branch --show-current) | Out-String).Trim()
    if ($branch -ne "main") { throw "Development source must be on main; found '$branch'." }
    $dirty = ((& git status --porcelain) | Out-String).Trim()
    if ($dirty) { throw "Development source has uncommitted changes. Commit/stash them before production deployment.`n$dirty" }
    $ReleaseSha = ((& git rev-parse HEAD) | Out-String).Trim()
}
finally { Pop-Location }

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ReleaseBackup = Join-Path $BackupRoot $stamp
$AppBackup = Join-Path $ReleaseBackup "application"
New-Item -ItemType Directory -Force -Path $AppBackup | Out-Null

Write-Host "Release: $ReleaseSha"
Write-Host "Creating application rollback snapshot: $AppBackup"
Invoke-Checked "robocopy" @($InstallRoot, $AppBackup, "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/XJ", "/XD", (Join-Path $InstallRoot ".venv"), (Join-Path $InstallRoot "node_modules"), (Join-Path $InstallRoot "apps\web\node_modules"), (Join-Path $InstallRoot "data"), (Join-Path $InstallRoot "backups"), (Join-Path $InstallRoot "logs"), "/XF", (Join-Path $InstallRoot "apps\api\.env"))
if ($LASTEXITCODE -gt 7) { throw "Could not create application rollback snapshot (robocopy exit $LASTEXITCODE)." }

Write-Host "Creating verified PostgreSQL backup before deployment..."
Invoke-Checked "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $InstallRoot "scripts\windows\run-backup.ps1"), "-InstallRoot", $InstallRoot, "-PythonExe", $PythonExe, "-BackupDir", (Join-Path $ReleaseBackup "database"), "-Retain", "30")

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$wasRunning = $service -and $service.Status -eq "Running"
try {
    if ($wasRunning) {
        Write-Host "Stopping $ServiceName..."
        Stop-Service -Name $ServiceName -ErrorAction Stop
        (Get-Service -Name $ServiceName).WaitForStatus("Stopped", "00:00:30")
    }

    Write-Host "Deploying application source while preserving production data/configuration..."
    # Never overwrite production .env, data, backups, logs, or virtual environments.
    Invoke-Checked "robocopy" @($SourceRoot, $InstallRoot, "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/XJ", "/XD", (Join-Path $SourceRoot ".git"), (Join-Path $SourceRoot ".venv"), (Join-Path $SourceRoot "node_modules"), (Join-Path $SourceRoot "apps\api\.venv"), (Join-Path $SourceRoot "apps\web\node_modules"), (Join-Path $SourceRoot "data"), (Join-Path $SourceRoot "backups"), (Join-Path $SourceRoot "logs"), (Join-Path $InstallRoot "data"), (Join-Path $InstallRoot "backups"), (Join-Path $InstallRoot "logs"), "/XF", (Join-Path $SourceRoot "apps\api\.env"), (Join-Path $InstallRoot "apps\api\.env"))
    if ($LASTEXITCODE -gt 7) { throw "Application copy failed (robocopy exit $LASTEXITCODE)." }

    Write-Host "Installing production Python dependencies..."
    Push-Location $ApiRoot
    try {
        Invoke-Checked $PythonExe @("-m", "pip", "install", "-r", "requirements.txt")
    }
    finally { Pop-Location }

    Write-Host "Building React production bundle..."
    Invoke-Checked "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $InstallRoot "scripts\windows\build-web.ps1"), "-InstallRoot", $InstallRoot)

    if (-not $SkipDatabaseMigration) {
        Write-Host "Applying controlled Alembic database lifecycle..."
        Invoke-Checked "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $InstallRoot "scripts\windows\upgrade-production-db.ps1"), "-InstallRoot", $InstallRoot, "-PythonExe", $PythonExe, "-BackupDir", (Join-Path $ReleaseBackup "database-pre-migration"), "-SkipServiceRestart")
    }

    if ($service) {
        Write-Host "Starting $ServiceName..."
        Start-Service -Name $ServiceName -ErrorAction Stop
        (Get-Service -Name $ServiceName).WaitForStatus("Running", "00:00:30")
    }

    Write-Host "Checking application readiness..."
    $ready = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/ready" -UseBasicParsing -TimeoutSec 30
    if ($ready.StatusCode -ne 200) { throw "Production readiness returned HTTP $($ready.StatusCode)." }

    $manifest = [ordered]@{
        deployed_at = (Get-Date).ToString("o")
        source_root = $SourceRoot
        release_sha = $ReleaseSha
        install_root = $InstallRoot
        service = $ServiceName
        database_migration = -not $SkipDatabaseMigration
        readiness = "HTTP 200"
        application_backup = $AppBackup
        database_backup = Join-Path $ReleaseBackup "database"
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ReleaseBackup "release-manifest.json") -Encoding UTF8
    Write-Host "DEPLOYMENT SUCCESSFUL"
    Write-Host "Release: $ReleaseSha"
    Write-Host "Rollback snapshot: $AppBackup"
    Write-Host "Readiness: HTTP 200"
}
catch {
    Write-Error "Deployment failed: $($_.Exception.Message)"
    Write-Host "The production database has NOT been automatically downgraded or restored."
    Write-Host "Application rollback snapshot: $AppBackup"
    Write-Host "Use scripts\windows\rollback-production.ps1 after reviewing the failure."
    if ($service) {
        $current = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($current -and $current.Status -eq "Stopped") {
            Write-Warning "$ServiceName is stopped after the failed deployment."
        }
    }
    throw
}
