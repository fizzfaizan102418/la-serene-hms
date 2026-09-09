[CmdletBinding()]
param(
    [string]$EnvFile = "C:\LaSereneHMS\apps\api\.env"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Production environment file not found: $EnvFile"
}

$values = @{}
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
    $parts = $trimmed.Split("=", 2)
    if ($parts.Count -eq 2) {
        $values[$parts[0].Trim()] = $parts[1].Trim()
    }
}

if ($values["HMS_ENVIRONMENT"] -ne "production") {
    throw "HMS_ENVIRONMENT must be production."
}

$databaseUrl = $values["HMS_DATABASE_URL"]
if (-not $databaseUrl -or -not $databaseUrl.StartsWith("postgresql")) {
    throw "HMS_DATABASE_URL must be a PostgreSQL URL in production."
}

$secret = $values["HMS_SECRET_KEY"]
if (-not $secret -or $secret.Length -lt 32 -or $secret -eq "la-serene-development-secret-change-me" -or $secret -like "*CHANGE_ME*") {
    throw "HMS_SECRET_KEY must be a non-default secret of at least 32 characters."
}

Write-Host "Production environment contract is valid."
Write-Host "Database: PostgreSQL"
Write-Host "Secret: configured (value not displayed)"
Write-Host "Environment: production"
