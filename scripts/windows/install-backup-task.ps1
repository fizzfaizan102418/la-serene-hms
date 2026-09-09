[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$TaskName = "La Serene HMS - PostgreSQL Backup",
    [string]$PythonExe = "",
    [string]$BackupDir = "",
    [int]$Retain = 7,
    [string]$RunAsUser = ""
)

$ErrorActionPreference = "Stop"

$Runner = Join-Path $InstallRoot "scripts\windows\run-backup.ps1"
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) { throw "Backup runner not found: $Runner" }
if (-not $PythonExe) { $PythonExe = Join-Path $InstallRoot ".venv\Scripts\python.exe" }
if (-not $BackupDir) { $BackupDir = Join-Path $InstallRoot "backups" }
if (-not $RunAsUser) { $RunAsUser = "$env:USERDOMAIN\$env:USERNAME" }

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -InstallRoot `"$InstallRoot`" -PythonExe `"$PythonExe`" -BackupDir `"$BackupDir`" -Retain $Retain"
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

$password = Read-Host "Enter the Windows password for scheduled-task account $RunAsUser" -AsSecureString
$credential = [System.Net.NetworkCredential]::new('', $password)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User $RunAsUser -Password $credential.Password -RunLevel Highest -Force | Out-Null

Write-Host "Registered '$TaskName' to run daily at 02:00 under $RunAsUser."
Write-Host "Backup directory: $BackupDir"
Write-Host "Retention: $Retain verified backups"
Write-Host "Test manually with: Start-ScheduledTask -TaskName '$TaskName'"
