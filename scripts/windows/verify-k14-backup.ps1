[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$BackupDir = "C:\LaSereneHMS\backups",
    [int]$Retain = 7
)

$ErrorActionPreference = "Stop"

$python = Join-Path $InstallRoot "venv\Scripts\python.exe"
$script = Join-Path $InstallRoot "ops\backup\scheduled.py"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Production Python executable was not found at the expected path."
}
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    throw "Phase J scheduled backup runner was not found."
}
if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) {
    throw "Backup directory was not found: $BackupDir"
}

& $python $script --output-dir $BackupDir --retain $Retain
if ($LASTEXITCODE -ne 0) {
    throw "Phase J backup creation/checksum verification failed with exit code $LASTEXITCODE."
}

Write-Host "K14 PASS: Phase J backup creation and checksum verification completed."
