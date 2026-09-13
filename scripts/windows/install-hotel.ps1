[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$NssmExe = "C:\Tools\nssm\nssm.exe",
    [string]$PostgresServiceName = "postgresql-x64-18",
    [string]$BackupTaskName = "La Serene HMS - PostgreSQL Backup",
    [switch]$RunAsSystem
)

$ErrorActionPreference = "Stop"

$ApiRoot = Join-Path $InstallRoot "apps\api"
$PythonExe = Join-Path $ApiRoot ".venv\Scripts\python.exe"
$EnvFile = Join-Path $ApiRoot ".env"

foreach ($required in @(
    @{ Path = $ApiRoot; Label = "API directory" },
    @{ Path = $PythonExe; Label = "Production Python executable" },
    @{ Path = $EnvFile; Label = "Production environment file" },
    @{ Path = $NssmExe; Label = "NSSM executable" }
)) {
    if (-not (Test-Path -LiteralPath $required.Path)) {
        throw "$($required.Label) not found: $($required.Path)"
    }
}

Write-Host "Installing La Serene HMS production service..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "install-api-service.ps1") `
    -InstallRoot $InstallRoot `
    -PythonExe $PythonExe `
    -NssmExe $NssmExe `
    -PostgresServiceName $PostgresServiceName
if ($LASTEXITCODE -ne 0) { throw "API service installation failed." }

Write-Host "Registering PostgreSQL backup task..."
$backupArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $PSScriptRoot "install-backup-task.ps1"),
    "-InstallRoot", $InstallRoot,
    "-PythonExe", $PythonExe,
    "-TaskName", $BackupTaskName
)
if ($RunAsSystem) { $backupArgs += "-RunAsSystem" }
& powershell.exe @backupArgs
if ($LASTEXITCODE -ne 0) { throw "Backup task installation failed." }

Write-Host "Running final read-only go-live validation..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "validate-go-live.ps1") -InstallRoot $InstallRoot -ServiceName "LaSereneHMSApi"
if ($LASTEXITCODE -ne 0) { throw "Go-live validation failed." }

Write-Host "HOTEL INSTALLATION COMPLETE"
Write-Host "Staff can use the browser/desktop shortcut; no Python, PowerShell, Git, or PostgreSQL knowledge is required."
