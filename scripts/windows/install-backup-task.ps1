[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$TaskName = "La Serene HMS - PostgreSQL Backup",
    [string]$PythonExe = "",
    [string]$BackupDir = "",
    [int]$Retain = 7,
    [string]$MirrorDir = "",
    [int]$MirrorRetain = 30,
    [string]$RunAsUser = "",
    [switch]$RunAsSystem
)

$ErrorActionPreference = "Stop"

$ApiRoot = Join-Path $InstallRoot "apps\api"
$Runner = Join-Path $InstallRoot "scripts\windows\run-backup.ps1"
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) { throw "Backup runner not found: $Runner" }
if (-not $PythonExe) { $PythonExe = Join-Path $ApiRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Production Python executable not found: $PythonExe" }
if (-not $BackupDir) { $BackupDir = Join-Path $InstallRoot "backups" }

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -InstallRoot `"$InstallRoot`" -PythonExe `"$PythonExe`" -BackupDir `"$BackupDir`" -Retain $Retain -MirrorRetain $MirrorRetain"
if ($MirrorDir) { $arguments += " -MirrorDir `"$MirrorDir`"" }

$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

if ($RunAsSystem) {
    $RunAsUser = "SYSTEM"
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest -Force | Out-Null
} else {
    if (-not $RunAsUser) { $RunAsUser = "$env:USERDOMAIN\$env:USERNAME" }
    $password = Read-Host "Enter the Windows password for scheduled-task account $RunAsUser" -AsSecureString
    $credential = [System.Net.NetworkCredential]::new('', $password)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User $RunAsUser -Password $credential.Password -RunLevel Highest -Force | Out-Null
}

Write-Host "Registered '$TaskName' to run daily at 02:00 under $RunAsUser."
Write-Host "Local backup directory: $BackupDir"
Write-Host "Local retention: $Retain verified backups"
if ($MirrorDir) {
    Write-Host "External/network mirror: $MirrorDir"
    Write-Host "Mirror retention: $MirrorRetain verified backups"
} else {
    Write-Host "External/network mirror: not configured"
    Write-Host "Configure HMS_BACKUP_MIRROR_DIR in apps\api\.env or pass -MirrorDir when installing the task."
}
Write-Host "Test manually with: Start-ScheduledTask -TaskName '$TaskName'"
