[CmdletBinding()]
param(
    [string]$EnvFile = "C:\LaSereneHMS\apps\api\.env"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Production environment file not found: $EnvFile"
}

# Use well-known SIDs so the script also works on non-English Windows editions.
# SYSTEM is the default account used by the HMS service installer; the built-in
# Administrators group retains administrative access to the production secret.
& icacls.exe $EnvFile /inheritance:r | Out-Null
& icacls.exe $EnvFile /grant:r "*S-1-5-18:F" "*S-1-5-32-544:F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restrict ACLs on $EnvFile"
}

Write-Host "Restricted production environment file permissions to SYSTEM and Administrators."
