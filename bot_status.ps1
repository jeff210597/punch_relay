param([int]$Tail = 30)
$serviceName = "PunchBotService"
$root = $PSScriptRoot
$svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "$serviceName is not installed." -ForegroundColor Yellow
    exit 1
}
$svc | Format-Table Name, Status, StartType -AutoSize
if (Test-Path (Join-Path $root "bot.log")) {
    Get-Content (Join-Path $root "bot.log") -Encoding UTF8 -Tail $Tail
}
