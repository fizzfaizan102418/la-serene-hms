[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PostgresServiceName,
    [string]$ReadinessUrl = "http://127.0.0.1:8000/api/ready",
    [int]$TimeoutSeconds = 90,
    [switch]$ConfirmDatabaseInterruption
)

$ErrorActionPreference = "Stop"

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

function Wait-Readiness([int]$ExpectedStatus, [int]$Timeout) {
    $deadline = (Get-Date).AddSeconds($Timeout)
    do {
        $code = Get-ReadinessStatusCode $ReadinessUrl
        if ($code -eq $ExpectedStatus) { return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "Expected readiness HTTP $ExpectedStatus but last observed HTTP $code at $ReadinessUrl"
}

$postgres = Get-Service -Name $PostgresServiceName -ErrorAction Stop
if ($postgres.Status -ne "Running") {
    throw "PostgreSQL service '$PostgresServiceName' is not running."
}

if (-not $ConfirmDatabaseInterruption) {
    throw "This test intentionally interrupts PostgreSQL. Re-run with -ConfirmDatabaseInterruption."
}

Wait-Readiness 200 $TimeoutSeconds
Write-Host "Healthy readiness confirmed (HTTP 200)."
Write-Host "Stopping PostgreSQL service '$PostgresServiceName' to verify outage detection."
Stop-Service -Name $PostgresServiceName -Force

try {
    Wait-Readiness 503 $TimeoutSeconds
    Write-Host "PASS: PostgreSQL outage produced readiness HTTP 503."
} finally {
    Write-Host "Starting PostgreSQL service '$PostgresServiceName'."
    Start-Service -Name $PostgresServiceName
}

Wait-Readiness 200 $TimeoutSeconds
Write-Host "PASS: PostgreSQL recovery returned readiness HTTP 200."
Write-Host "No database migration or restore operation was performed by this verifier."
