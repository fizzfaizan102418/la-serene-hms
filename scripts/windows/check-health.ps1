[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

function Test-Endpoint([string]$Path, [int]$ExpectedStatus) {
    $response = Invoke-WebRequest -Uri "$BaseUrl$Path" -UseBasicParsing -TimeoutSec 10
    if ([int]$response.StatusCode -ne $ExpectedStatus) {
        throw "$Path returned HTTP $($response.StatusCode); expected $ExpectedStatus"
    }
    return $response
}

$health = Test-Endpoint "/api/health" 200
$ready = Test-Endpoint "/api/ready" 200

Write-Host "HMS liveness: $($health.StatusCode)"
Write-Host "HMS readiness: $($ready.StatusCode)"
Write-Host "Production service is healthy and database-ready."
