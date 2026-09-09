[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\LaSereneHMS",
    [string]$NpmExe = "npm"
)

$ErrorActionPreference = "Stop"
$WebRoot = Join-Path $InstallRoot "apps\web"

if (-not (Test-Path -LiteralPath $WebRoot -PathType Container)) {
    throw "Web application directory not found: $WebRoot"
}

Push-Location $WebRoot
try {
    & $NpmExe install
    if ($LASTEXITCODE -ne 0) {
        throw "npm install failed with exit code $LASTEXITCODE"
    }

    & $NpmExe run build
    if ($LASTEXITCODE -ne 0) {
        throw "npm run build failed with exit code $LASTEXITCODE"
    }

    $Dist = Join-Path $WebRoot "dist"
    if (-not (Test-Path -LiteralPath $Dist -PathType Container)) {
        throw "Frontend build completed without creating $Dist"
    }

    Write-Host "Production React build created at $Dist"
}
finally {
    Pop-Location
}
