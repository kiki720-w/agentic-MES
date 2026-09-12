param(
    [string]$PostgresRoot = "D:\PostgreSQLPortable\17.11\pgsql",
    [string]$DataDirectory = "D:\PostgreSQLPortable\data-agentic-mes",
    [int]$Port = 55432
)

$ErrorActionPreference = "Stop"
$pgCtl = Join-Path $PostgresRoot "bin\pg_ctl.exe"
$pgIsReady = Join-Path $PostgresRoot "bin\pg_isready.exe"

if (-not (Test-Path -LiteralPath $pgCtl)) {
    throw "PostgreSQL executable not found: $pgCtl"
}
if (-not (Test-Path -LiteralPath (Join-Path $DataDirectory "PG_VERSION"))) {
    throw "PostgreSQL data directory is not initialized: $DataDirectory"
}

& $pgIsReady -h 127.0.0.1 -p $Port *> $null
if ($LASTEXITCODE -ne 0) {
    $logPath = Join-Path (Split-Path $DataDirectory -Parent) "agentic-mes-postgres.log"
    & $pgCtl start -D $DataDirectory -l $logPath
}

& $pgIsReady -h 127.0.0.1 -p $Port
