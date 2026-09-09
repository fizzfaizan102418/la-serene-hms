[CmdletBinding()]
param(
    [string]$RuleName = "La Serene HMS API",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existing) {
    Remove-NetFirewallRule -DisplayName $RuleName
}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Description "Allow La Serene HMS API access from the hotel LAN" `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -Profile Domain,Private `
    -RemoteAddress LocalSubnet | Out-Null

Write-Host "Configured Windows Firewall rule '$RuleName' for TCP/$Port from LocalSubnet on Domain/Private profiles."
