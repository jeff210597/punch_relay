param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$serviceName = "PunchBotService"
$root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$syncFlag = Join-Path $root "synced.flag"

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "Please run this script as Administrator." -ForegroundColor Yellow
        exit 1
    }
}

Assert-Admin
Set-Location $root

if (Test-Path $syncFlag) {
    Remove-Item -LiteralPath $syncFlag -Force
    Write-Host "Removed synced.flag. Discord commands will resync on startup." -ForegroundColor Yellow
}

Write-Host "Restarting $serviceName with Discord command resync..." -ForegroundColor Cyan
Restart-Service -Name $serviceName -Force
Start-Sleep -Seconds 8

$svc = Get-Service -Name $serviceName
Write-Host "Service status: $($svc.Status)" -ForegroundColor Green

$logPath = Join-Path $root "bot.log"
if (Test-Path $logPath) {
    Write-Host ""
    Write-Host "Latest bot.log:" -ForegroundColor Cyan
    Get-Content -LiteralPath $logPath -Encoding UTF8 -Tail 25
}

if (-not $NoPause) {
    Write-Host ""
    Read-Host "Press Enter to close"
}
