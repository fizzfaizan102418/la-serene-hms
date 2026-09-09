[CmdletBinding()]
param(
    [string]$ServiceName = "LaSereneHMSApi",
    [string]$ReadinessUrl = "http://127.0.0.1:8000/api/ready",
    [int]$TimeoutSeconds = 60,
    [switch]$ConfirmUnexpectedTermination
)

$ErrorActionPreference = "Stop"

function Get-ReadinessStatusCode([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
        return [int]$response.StatusCode
    } catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
    }
}

function Wait-Readiness([int]$ExpectedStatus, [int]$Timeout) {
    $deadline = (Get-Date).AddSeconds($Timeout)
    do {
        $code = Get-ReadinessStatusCode $ReadinessUrl
        if ($code -eq $ExpectedStatus) { return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "Expected readiness HTTP $ExpectedStatus but last observed HTTP $code at $ReadinessUrl"
}

$service = Get-Service -Name $ServiceName -ErrorAction Stop
if ($service.Status -ne "Running") {
    throw "API service '$ServiceName' is not running. Start it before recovery verification."
}

if (-not $ConfirmUnexpectedTermination) {
    throw "This test intentionally terminates the API process. Re-run with -ConfirmUnexpectedTermination."
}

$serviceCim = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if (-not $serviceCim -or [int]$serviceCim.ProcessId -le 0) {
    throw "Could not resolve the running API process for '$ServiceName'."
}

Wait-Readiness 200 $TimeoutSeconds
$processId = [int]$serviceCim.ProcessId
Write-Host "Healthy readiness confirmed (HTTP 200)."
Write-Host "Terminating API process PID $processId to verify NSSM recovery."
Stop-Process -Id $processId -Force

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    $service = Get-Service -Name $ServiceName -ErrorAction Stop
    if ($service.Status -eq "Running") {
        $current = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
        if ($current -and [int]$current.ProcessId -gt 0 -and [int]$current.ProcessId -ne $processId) {
            Wait-Readiness 200 $TimeoutSeconds
            Write-Host "PASS: unexpected API termination was recovered by the Windows service."
            Write-Host "Old PID: $processId; recovered PID: $([int]$current.ProcessId)"
            exit 0
        }
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

throw "API service did not recover from the intentional process termination within $TimeoutSeconds seconds."
