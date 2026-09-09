[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$ApiServiceName = "LaSereneHMSApi",
    [string]$BackupTaskName = "La Serene HMS - PostgreSQL Backup",
    [string]$ReadinessUrl = "http://127.0.0.1:8000/api/ready"
)

$ErrorActionPreference = "Stop"

function Require-Path([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label not found: $Path"
    }
}

Write-Host "K13 production recovery prerequisite verification"

$envFile = Join-Path $InstallRoot "apps\api\.env"
$logDir = Join-Path $InstallRoot "logs"
$backupDir = Join-Path $InstallRoot "backups"
Require-Path $envFile "Production environment file"
Require-Path $logDir "Production log directory"
Require-Path $backupDir "Backup directory"

$apiService = Get-Service -Name $ApiServiceName -ErrorAction Stop
if ($apiService.StartType -ne "Automatic") {
    throw "API service '$ApiServiceName' is not configured for automatic startup."
}

$backupTask = Get-ScheduledTask -TaskName $BackupTaskName -ErrorAction Stop
if ($backupTask.State -eq "Disabled") {
    throw "Scheduled backup task '$BackupTaskName' is disabled."
}

$stdoutLog = Join-Path $logDir "api.stdout.log"
$stderrLog = Join-Path $logDir "api.stderr.log"
Require-Path $stdoutLog "API stdout log"
Require-Path $stderrLog "API stderr log"

try {
    $response = Invoke-WebRequest -Uri $ReadinessUrl -UseBasicParsing -TimeoutSec 10
    $code = [int]$response.StatusCode
} catch {
    $code = if ($_.Exception.Response -and $_.Exception.Response.StatusCode) { [int]$_.Exception.Response.StatusCode } else { 0 }
}

if ($code -ne 200) {
    throw "Production readiness is HTTP $code; expected HTTP 200."
}

Write-Host "PASS: API service is Automatic."
Write-Host "PASS: scheduled backup task is enabled."
Write-Host "PASS: persistent API logs exist."
Write-Host "PASS: /api/ready returned HTTP 200."
Write-Host "Reboot verification remains an operator-controlled test: reboot the production host during an approved maintenance window, then rerun this script and confirm the backup task and API readiness are healthy."
