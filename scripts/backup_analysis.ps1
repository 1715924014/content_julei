param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$DbPath = "data\analysis.db",
    [string]$LogDir = "logs",
    [string]$BackupRoot = "backups"
)

$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $BackupRoot $timestamp
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$resolvedDbPath = Join-Path $ProjectRoot $DbPath
if (-not (Test-Path $resolvedDbPath)) {
    Write-Error "analysis.db not found at $resolvedDbPath"
}

Copy-Item -Path $resolvedDbPath -Destination (Join-Path $backupDir "analysis.db") -Force

$resolvedLogDir = Join-Path $ProjectRoot $LogDir
if (Test-Path $resolvedLogDir) {
    Copy-Item -Path $resolvedLogDir -Destination (Join-Path $backupDir "logs") -Recurse -Force
}

Write-Host "Backup written to $backupDir"
exit 0
