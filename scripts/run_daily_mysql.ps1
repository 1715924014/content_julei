param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$ConfigPath = "config\mysql.example.json",
    [string]$DbPath = "data\analysis.db",
    [string]$LogDir = "logs",
    [string]$BackupRoot = "backups",
    [int]$LogRetentionDays = 90,
    [int]$Limit = 10000,
    [int]$MaxDurationSeconds = 0,
    [double]$MinThroughputRowsPerSecond = 0,
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"

if (-not $env:MINI_PROGRAM_DB_PASSWORD) {
    Write-Error "MINI_PROGRAM_DB_PASSWORD is required before running the daily MySQL import."
}

Set-Location $ProjectRoot

& $PythonCommand -m src.suggestion_pipeline doctor `
    --config $ConfigPath `
    --db $DbPath `
    --backup-root $BackupRoot
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$DailyArgs = @(
    "-m", "src.suggestion_pipeline", "run-daily-mysql",
    "--config", $ConfigPath,
    "--db", $DbPath,
    "--log-dir", $LogDir,
    "--limit", $Limit
)
if ($MaxDurationSeconds -gt 0) {
    $DailyArgs += @("--max-duration-seconds", $MaxDurationSeconds)
}
if ($MinThroughputRowsPerSecond -gt 0) {
    $DailyArgs += @("--min-throughput-rows-per-second", $MinThroughputRowsPerSecond)
}

& $PythonCommand @DailyArgs
$DailyExitCode = $LASTEXITCODE

if ($LogRetentionDays -gt 0) {
    $LogCutoff = (Get-Date).AddDays(-$LogRetentionDays)
    Get-ChildItem -Path $LogDir -Filter "daily-mysql-*.json" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $LogCutoff } |
        Remove-Item -Force
}

exit $DailyExitCode
