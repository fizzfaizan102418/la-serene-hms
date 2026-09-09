[CmdletBinding()]
param(
    [string]$EnvFile = "C:\LaSereneHMS\apps\api\.env"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Production environment file not found: $EnvFile"
}

# Keep the secret-bearing file readable by Windows administrators and SYSTEM,
# which is the default account used by the HMS service installer.
& icacls.exe $EnvFile /inheritance:r | Out-Null
& icacls.exe $EnvFile /grant:r "SYSTEM:F" "Administrators:F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restrict ACLs on $EnvFile"
}

Write-Host "Restricted production environment file permissions to SYSTEM and Administrators."
