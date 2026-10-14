param([switch]$Fixture, [int]$BasePort = 8010)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONUTF8 = '1'
uv sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
if (-not (Test-Path -LiteralPath '.env.pilot')) {
    uv run --no-sync campus init-pilot
    if ($LASTEXITCODE -ne 0) { throw 'Pilot initialization failed' }
}
$env:CAMPUS_ENV_FILE = Join-Path (Get-Location) '.env.pilot'
if ($Fixture) {
    uv run --no-sync campus all --fixture --base-port $BasePort
} else {
    uv run --no-sync campus all --base-port $BasePort
}
