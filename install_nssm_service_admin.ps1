param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$serviceName = "PunchBotService"
$root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$python = $null
$nssm = Join-Path $root "tools\nssm\win32\nssm.exe"
$logPath = Join-Path $root "install_nssm_service.log"

if ([Environment]::Is64BitOperatingSystem) {
    $nssm = Join-Path $root "tools\nssm\win64\nssm.exe"
}

$pythonCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
)

foreach ($candidate in $pythonCandidates) {
    if (Test-Path -LiteralPath $candidate) {
        $python = $candidate
        break
    }
}

if (-not $python) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $python = $pythonCmd.Source
    }
}

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Write-Host $line
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value $line
}

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Please run this script as Administrator."
    }
}

Set-Location $root
Set-Content -LiteralPath $logPath -Encoding UTF8 -Value "PunchBotService NSSM install log"

Assert-Admin
Write-Log "Running as Administrator."
Write-Log "Using NSSM: $nssm"

if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    throw "Python not found: $python"
}
if (-not (Test-Path -LiteralPath $nssm)) {
    throw "NSSM not found: $nssm"
}

& $python -m py_compile "bot_all_in_one.py"
Write-Log "Python syntax check passed."

$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Log "Service exists; stopping before reconfiguring."
    if ($existing.Status -ne "Stopped") {
        Stop-Service -Name $serviceName -Force
        Start-Sleep -Seconds 3
    }
} else {
    Write-Log "Installing NSSM service."
    & $nssm install $serviceName $python "bot_all_in_one.py" | ForEach-Object { Write-Log $_ }
}

& $nssm set $serviceName AppDirectory $root | ForEach-Object { Write-Log $_ }
& $nssm set $serviceName DisplayName "Punch Relay Discord Bot" | ForEach-Object { Write-Log $_ }
& $nssm set $serviceName Description "Runs the punch_relay Discord bot." | ForEach-Object { Write-Log $_ }
& $nssm set $serviceName Start SERVICE_AUTO_START | ForEach-Object { Write-Log $_ }
& $nssm set $serviceName AppStopMethodConsole 1500 | ForEach-Object { Write-Log $_ }
& $nssm set $serviceName AppThrottle 5000 | ForEach-Object { Write-Log $_ }

$syncFlag = Join-Path $root "synced.flag"
if (Test-Path -LiteralPath $syncFlag) {
    Remove-Item -LiteralPath $syncFlag -Force
    Write-Log "Removed synced.flag to force Discord command resync."
}

Write-Log "Starting service."
Start-Service -Name $serviceName
Start-Sleep -Seconds 12

$svc = Get-Service -Name $serviceName
Write-Log "Service status: $($svc.Status)"

$botLog = Join-Path $root "bot.log"
if (Test-Path -LiteralPath $botLog) {
    Write-Log "Latest bot.log tail:"
    Get-Content -LiteralPath $botLog -Encoding UTF8 -Tail 40 | ForEach-Object {
        Add-Content -LiteralPath $logPath -Encoding UTF8 -Value $_
        Write-Host $_
    }
}

if (-not $NoPause) {
    Read-Host "Press Enter to close"
}
