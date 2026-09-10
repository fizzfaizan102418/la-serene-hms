[CmdletBinding()]
param(
    [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin",
    [string]$PgHost = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 5432,
    [string]$Database = "la_serene_hms",
    [string]$AppUser = "la_serene_hms_app"
)

$ErrorActionPreference = "Stop"

if ($Database -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
    throw "Database name must contain only letters, numbers, and underscores and must not start with a number."
}
if ($AppUser -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
    throw "Application username must contain only letters, numbers, and underscores and must not start with a number."
}

$Psql = Join-Path $PgBin "psql.exe"
if (-not (Test-Path $Psql)) {
    throw "PostgreSQL client not found at '$Psql'. Install PostgreSQL first or pass -PgBin with the PostgreSQL bin directory."
}

Write-Host "La Serene HMS - PostgreSQL production bootstrap"
Write-Host "Host: $PgHost`:$Port  Database: $Database  User: $AppUser"
Write-Host ""

$AdminPassword = Read-Host "PostgreSQL administrator password" -AsSecureString
$AdminCredential = New-Object System.Management.Automation.PSCredential("postgres", $AdminPassword)
$AdminPlain = $AdminCredential.GetNetworkCredential().Password

$env:PGPASSWORD = $AdminPlain
try {
    & $Psql -h $PgHost -p $Port -U postgres -d postgres -v ON_ERROR_STOP=1 -c "SELECT version();" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL connection test failed." }

    $AppPassword = Read-Host "Password for $AppUser (do not reuse your PostgreSQL administrator password)" -AsSecureString
    $AppCredential = New-Object System.Management.Automation.PSCredential($AppUser, $AppPassword)
    $AppPlain = $AppCredential.GetNetworkCredential().Password

    if ([string]::IsNullOrWhiteSpace($AppPlain) -or $AppPlain.Length -lt 20) {
        throw "Application database password must be at least 20 characters."
    }

    $RoleExists = ((& $Psql -h $PgHost -p $Port -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$AppUser';") | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not check whether the application role exists." }

    if ($RoleExists -ne "1") {
        & $Psql -h $PgHost -p $Port -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE $AppUser LOGIN;" | Out-Host
    } else {
        & $Psql -h $PgHost -p $Port -U postgres -d postgres -v ON_ERROR_STOP=1 -c "ALTER ROLE $AppUser WITH LOGIN;" | Out-Host
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create/update the application role." }

    # Set the password through psql's interactive \password command so the secret
    # is not exposed in SQL text or the process command line.
    $PasswordInput = "$AppPlain`n$AppPlain`n"
    $PasswordInput | & $Psql -h $PgHost -p $Port -U postgres -d postgres -v ON_ERROR_STOP=1 -c "\password $AppUser" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Could not set the application role password." }

    $DatabaseExists = ((& $Psql -h $PgHost -p $Port -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$Database';") | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not check whether the application database exists." }
    if ($DatabaseExists -ne "1") {
        & $Psql -h $PgHost -p $Port -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $Database OWNER $AppUser;" | Out-Host
    } else {
        & $Psql -h $PgHost -p $Port -U postgres -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $Database OWNER TO $AppUser;" | Out-Host
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create/update the application database." }

    & $Psql -h $PgHost -p $Port -U postgres -d $Database -v ON_ERROR_STOP=1 -c "REVOKE ALL ON DATABASE $Database FROM PUBLIC; GRANT CONNECT ON DATABASE $Database TO $AppUser;" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Could not apply database access policy." }

    $ConnectionUrl = "postgresql+psycopg://${AppUser}:<URL_ENCODED_PASSWORD>@$PgHost`:$Port/$Database"
    Write-Host ""
    Write-Host "Bootstrap completed successfully." -ForegroundColor Green
    Write-Host "Set HMS_DATABASE_URL in the production environment to:" -ForegroundColor Cyan
    Write-Host $ConnectionUrl
    Write-Host "Replace <URL_ENCODED_PASSWORD> with the URL-encoded application password. Do not commit the real value."
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Variable AdminPlain,AppPlain,AppPassword,AdminCredential,AppCredential,PasswordInput -ErrorAction SilentlyContinue
}
