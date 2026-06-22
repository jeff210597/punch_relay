param([switch]$NoPause)
$ErrorActionPreference = "Stop"
$serviceName = "PunchBotService"
$root = $PSScriptRoot

$runtimeState = Join-Path $root "bot_runtime_state.json"

$plannedStop = @{
    state = "clean_exit"
    reason = "planned restart via restart_bot.ps1"
    pid = $PID
    updated_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
} | ConvertTo-Json

Set-Content -LiteralPath $runtimeState -Value $plannedStop -Encoding UTF8

& sc.exe stop $serviceName | Out-Null
$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Seconds 1
    $svc = Get-Service -Name $serviceName -ErrorAction Stop
} while ($svc.Status -ne "Stopped" -and (Get-Date) -lt $deadline)
if ($svc.Status -ne "Stopped") { throw "$serviceName did not stop within 30 seconds." }

& sc.exe start $serviceName | Out-Null
$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Seconds 1
    $svc = Get-Service -Name $serviceName -ErrorAction Stop
} while ($svc.Status -ne "Running" -and (Get-Date) -lt $deadline)
if ($svc.Status -ne "Running") { throw "$serviceName did not start within 30 seconds." }
Write-Host "Service status: $($svc.Status)" -ForegroundColor Green
if (Test-Path (Join-Path $root "bot.log")) {
    Get-Content (Join-Path $root "bot.log") -Encoding UTF8 -Tail 20
}
if (-not $NoPause) { Read-Host "Press Enter to close" }
