[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [Parameter(Mandatory = $true)]
    [string]$ManifestFile,
    [Parameter(Mandatory = $true)]
    [string]$TargetDatabaseUrl,
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$PythonExe = "",
    [string]$Report = ""
)

$ErrorActionPreference = "Stop"

if (-not $PythonExe) { $PythonExe = Join-Path $InstallRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Python executable not found: $PythonExe" }
if (-not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) { throw "Backup file not found: $BackupFile" }
if (-not (Test-Path -LiteralPath $ManifestFile -PathType Leaf)) { throw "Manifest file not found: $ManifestFile" }

$env:PYTHONPATH = $InstallRoot
Push-Location $InstallRoot
try {
    $arguments = @(
        "-m", "ops.backup.recover",
        $BackupFile,
        $ManifestFile,
        "--target-database-url", $TargetDatabaseUrl,
        "--confirm"
    )
    if ($Report) { $arguments += @("--report", $Report) }
    & $PythonExe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Recovery verification failed. Inspect the command output and recovery database." }
} finally {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Pop-Location
}
