[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$ServiceName = "LaSereneHMSApi",
    [string]$ReadinessUrl = "http://127.0.0.1:8000/api/ready",
    [string]$BackupDir = "C:\LaSereneHMS\backups",
    [switch]$RequireBackup,
    [switch]$RequireRecoveryEvidence
)

$ErrorActionPreference = "Stop"

function Test-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-ReadinessStatusCode([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
        return [int]$response.StatusCode
    } catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
    }
}

function Assert-Check([string]$Name, [bool]$Condition, [string]$FailureMessage) {
    if ($Condition) {
        Write-Host "PASS: $Name"
        return
    }
    throw "FAIL: $Name — $FailureMessage"
}

Write-Host "K14 production go-live validation"
Write-Host "Install root: $InstallRoot"
Write-Host "Service: $ServiceName"

Assert-Check "Install root exists" (Test-Path -LiteralPath $InstallRoot -PathType Container) "Production install root was not found."

$service = Get-Service -Name $ServiceName -ErrorAction Stop
Assert-Check "API service exists" ($null -ne $service) "Windows service is not installed."
Assert-Check "API service is running" ($service.Status -eq "Running") "Start and validate the service before go-live."

$apiStdout = Join-Path $InstallRoot "logs\api.stdout.log"
$apiStderr = Join-Path $InstallRoot "logs\api.stderr.log"
Assert-Check "Persistent stdout log exists" (Test-Path -LiteralPath $apiStdout -PathType Leaf) "API stdout log is missing."
Assert-Check "Persistent stderr log exists" (Test-Path -LiteralPath $apiStderr -PathType Leaf) "API stderr log is missing."

$readiness = Get-ReadinessStatusCode $ReadinessUrl
Assert-Check "Database/application readiness" ($readiness -eq 200) "Expected HTTP 200 from $ReadinessUrl; observed HTTP $readiness."

$task = Get-ScheduledTask -TaskName "La Serene HMS - PostgreSQL Backup" -ErrorAction SilentlyContinue
Assert-Check "Scheduled backup task exists" ($null -ne $task) "The Phase J Windows backup task is not installed."

if ($RequireBackup) {
    Assert-Check "Backup directory exists" (Test-Path -LiteralPath $BackupDir -PathType Container) "Backup directory is missing."
    $recentBackup = Get-ChildItem -LiteralPath $BackupDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -eq ".dump" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    Assert-Check "A PostgreSQL backup exists" ($null -ne $recentBackup) "No .dump backup was found. Verify the Phase J backup workflow."
}

if ($RequireRecoveryEvidence) {
    $recoveryReport = Join-Path $InstallRoot "recovery\k11-recovery-report.json"
    Assert-Check "Recovery evidence exists" (Test-Path -LiteralPath $recoveryReport -PathType Leaf) "K11 recovery evidence was not found at the expected location."
}

Write-Host "K14 PASS: production prerequisites are satisfied."
Write-Host "This validator does not modify the database, restart the service, run migrations, or restore backups."
