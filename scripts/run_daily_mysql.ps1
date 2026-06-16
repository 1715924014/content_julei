param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$ConfigPath = "config\mysql.example.json",
    [string]$DbPath = "data\analysis.db",
    [string]$LogDir = "logs",
    [int]$Limit = 10000,
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"

if (-not $env:MINI_PROGRAM_DB_PASSWORD) {
    Write-Error "MINI_PROGRAM_DB_PASSWORD is required before running the daily MySQL import."
}

Set-Location $ProjectRoot

& $PythonCommand -m src.suggestion_pipeline run-daily-mysql `
    --config $ConfigPath `
    --db $DbPath `
    --log-dir $LogDir `
    --limit $Limit

exit $LASTEXITCODE
