[CmdletBinding()]
param(
    [string]$BackupDir = "C:\LaSereneHMS\backups",
    [Parameter(Mandatory = $true)]
    [string]$OffsiteDir,
    [int]$KeepLocal = 7
)

$ErrorActionPreference = "Stop"

function Require-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label not found: $Path"
    }
}

Require-File $BackupDir "Local backup directory"
New-Item -ItemType Directory -Force -Path $OffsiteDir | Out-Null

$latestDump = Get-ChildItem -LiteralPath $BackupDir -Filter "*.dump" -File |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if (-not $latestDump) {
    throw "No PostgreSQL backup dump found in $BackupDir"
}

$manifest = [System.IO.Path]::ChangeExtension($latestDump.FullName, ".manifest.json")
Require-File $manifest "Backup manifest"

$manifestData = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
if ($manifestData.status -ne "verified") {
    throw "Latest backup is not marked verified: $($latestDump.Name)"
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $latestDump.FullName).Hash.ToLowerInvariant()
if ($manifestData.sha256.ToLowerInvariant() -ne $hash) {
    throw "SHA-256 verification failed for $($latestDump.Name)"
}

Copy-Item -LiteralPath $latestDump.FullName -Destination (Join-Path $OffsiteDir $latestDump.Name) -Force
Copy-Item -LiteralPath $manifest -Destination (Join-Path $OffsiteDir ([System.IO.Path]::GetFileName($manifest))) -Force

$offsiteDump = Join-Path $OffsiteDir $latestDump.Name
$offsiteManifest = Join-Path $OffsiteDir ([System.IO.Path]::GetFileName($manifest))
Require-File $offsiteDump "Off-site backup copy"
Require-File $offsiteManifest "Off-site manifest copy"

$offsiteHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $offsiteDump).Hash.ToLowerInvariant()
if ($offsiteHash -ne $hash) {
    throw "Off-site SHA-256 verification failed for $($latestDump.Name)"
}

Get-ChildItem -LiteralPath $OffsiteDir -Filter "*.dump" -File |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -Skip $KeepLocal |
    Remove-Item -Force

Get-ChildItem -LiteralPath $OffsiteDir -Filter "*.manifest.json" -File |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -Skip $KeepLocal |
    Remove-Item -Force

[pscustomobject]@{
    status = "verified"
    backup_file = $latestDump.Name
    offsite_directory = $OffsiteDir
    sha256 = $hash
} | ConvertTo-Json -Compress
