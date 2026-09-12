param(
    [int]$DatabasePort = 55432,
    [int]$ApiPort = 8000
)

$ErrorActionPreference = "Stop"
$coreDirectory = Split-Path $PSScriptRoot -Parent
$venvDirectory = Join-Path $coreDirectory ".venv"

& (Join-Path $PSScriptRoot "start-postgres-dev.ps1") -Port $DatabasePort

$env:AUTONOMOUS_MES_DATABASE_URL = "postgresql+psycopg://mes@127.0.0.1:$DatabasePort/agentic_mes"
$env:AUTONOMOUS_MES_STORAGE_BACKEND = "postgresql"

Push-Location $coreDirectory
try {
    & (Join-Path $venvDirectory "Scripts\alembic.exe") upgrade head
    & (Join-Path $venvDirectory "Scripts\uvicorn.exe") autonomous_mes.api:app `
        --host 127.0.0.1 --port $ApiPort
}
finally {
    Pop-Location
}
