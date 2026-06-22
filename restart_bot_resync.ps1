param([switch]$NoPause)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$flag = Join-Path $root "synced.flag"
if (Test-Path $flag) { Remove-Item -LiteralPath $flag -Force }
& (Join-Path $root "restart_bot.ps1") -NoPause
if (-not $NoPause) { Read-Host "Press Enter to close" }

