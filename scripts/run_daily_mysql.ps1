param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$ConfigPath = "config\mysql.example.json",
    [string]$DbPath = "data\analysis.db",
    [string]$LogDir = "logs",
    [string]$BackupRoot = "backups",
    [string]$RecommendationOutputDir = "data",
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
if ($LogRetentionDays -lt 0) {
    Write-Error "LogRetentionDays must be zero or positive."
}
if ($Limit -le 0) {
    Write-Error "Limit must be a positive integer."
}
if ($MaxDurationSeconds -lt 0) {
    Write-Error "MaxDurationSeconds must be zero or positive."
}
if ($MinThroughputRowsPerSecond -lt 0) {
    Write-Error "MinThroughputRowsPerSecond must be zero or positive."
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
    "--recommendation-output-dir", $RecommendationOutputDir,
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
    try {
        $LogCutoff = (Get-Date).AddDays(-$LogRetentionDays)
        Get-ChildItem -Path $LogDir -Filter "daily-mysql-*.json" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -lt $LogCutoff } |
            Remove-Item -Force -ErrorAction Stop
    }
    catch {
        Write-Warning "Daily MySQL log cleanup failed: $($_.Exception.Message)"
    }
}

exit $DailyExitCode
