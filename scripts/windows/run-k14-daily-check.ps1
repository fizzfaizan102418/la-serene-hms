[CmdletBinding()]
param(
    [string]$ServiceName = "LaSereneHMSApi",
    [string]$ReadinessUrl = "http://127.0.0.1:8000/api/ready",
    [string]$BackupDir = "C:\LaSereneHMS\backups"
)

$ErrorActionPreference = "Stop"

$service = Get-Service -Name $ServiceName -ErrorAction Stop
if ($service.Status -ne "Running") {
    throw "Daily check failed: API service '$ServiceName' is not running."
}

try {
    $response = Invoke-WebRequest -Uri $ReadinessUrl -UseBasicParsing -TimeoutSec 10
    if ([int]$response.StatusCode -ne 200) {
        throw "Daily check failed: readiness returned HTTP $([int]$response.StatusCode)."
    }
} catch {
    throw "Daily check failed: readiness endpoint is not healthy."
}

$latestBackup = Get-ChildItem -LiteralPath $BackupDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -eq ".dump" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if ($null -eq $latestBackup) {
    throw "Daily check failed: no PostgreSQL backup file was found in $BackupDir."
}

$result = [ordered]@{
    status = "ok"
    service = $ServiceName
    readiness = 200
    latest_backup = $latestBackup.Name
    latest_backup_time = $latestBackup.LastWriteTime.ToString("o")
}

$result | ConvertTo-Json -Compress
