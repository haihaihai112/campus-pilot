param([switch]$Fixture, [int]$BasePort = 8010)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONUTF8 = '1'
uv sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
if ($Fixture) {
    uv run --no-sync campus all --fixture --base-port $BasePort
} else {
    uv run --no-sync campus all --base-port $BasePort
}
