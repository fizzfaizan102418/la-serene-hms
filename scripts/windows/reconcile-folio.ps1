[CmdletBinding()]
param(
    [int]$FolioId = 7,
    [string]$ApiBaseUrl = "http://127.0.0.1:8001",
    [string]$Username = "admin"
)

$ErrorActionPreference = "Stop"

Write-Host "La Serene HMS - Folio reconciliation" -ForegroundColor Cyan
Write-Host "API: $ApiBaseUrl"
Write-Host "Folio: #$FolioId"
Write-Host ""

$passwordSecure = Read-Host "Development admin password" -AsSecureString
$password = [System.Net.NetworkCredential]::new("", $passwordSecure).Password

try {
    $login = Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/api/auth/login" -ContentType "application/json" -Body (@{
        username = $Username
        password = $password
    } | ConvertTo-Json)

    if (-not $login.access_token) {
        throw "Login succeeded but no access token was returned."
    }

    $headers = @{ Authorization = "Bearer $($login.access_token)" }

    $before = Invoke-RestMethod -Method Get -Uri "$ApiBaseUrl/api/folios/$FolioId/integrity" -Headers $headers
    Write-Host "Before:" -ForegroundColor Yellow
    $before | Format-List

    if ($before.ok -eq $true) {
        Write-Host "Folio is already financially consistent. No repair was performed." -ForegroundColor Green
        exit 0
    }

    $result = Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/api/folios/$FolioId/reconcile-and-reopen" -Headers $headers

    Write-Host "Reconciliation result:" -ForegroundColor Green
    $result | Format-List

    $after = Invoke-RestMethod -Method Get -Uri "$ApiBaseUrl/api/folios/$FolioId/integrity" -Headers $headers
    Write-Host "After:" -ForegroundColor Green
    $after | Format-List

    if ($after.ok -ne $true) {
        throw "Folio remains financially inconsistent after reconciliation. Do not take payment yet."
    }

    Write-Host ""
    Write-Host "PASS: Folio #$FolioId is reconciled and reopened. Return to Billing and settle the remaining balance normally." -ForegroundColor Green
}
finally {
    $password = $null
    $passwordSecure = $null
}
