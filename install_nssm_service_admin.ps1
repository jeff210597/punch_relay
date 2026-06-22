param(
    [string]$ControlUser = "$env:COMPUTERNAME\7c",
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$serviceName = "PunchBotService"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$nssmArch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
$nssm = Join-Path $root "tools\nssm\$nssmArch\nssm.exe"
$logPath = Join-Path $root "install_nssm_service.log"

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "This one-time setup must be run as Administrator."
    }
}

function Write-Log([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Write-Host $line
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value $line
}

function Grant-ServiceControl([string]$Account) {
    $sid = ([Security.Principal.NTAccount]$Account).Translate([Security.Principal.SecurityIdentifier]).Value
    $sddl = ((& sc.exe sdshow $serviceName) | Where-Object { $_ -match '^D:' } | Select-Object -First 1).Trim()
    if (-not $sddl) { throw "Unable to read the service security descriptor." }
    if ($sddl -match [regex]::Escape($sid)) {
        Write-Log "$Account already has service control permission."
        return
    }

    # Query config/status plus start, stop, pause/continue and interrogate; no config/delete rights.
    $ace = "(A;;CCLCSWRPWPDTLOCRRC;;;$sid)"
    $systemAclIndex = $sddl.IndexOf("S:")
    $updated = if ($systemAclIndex -ge 0) {
        $sddl.Insert($systemAclIndex, $ace)
    } else {
        "$sddl$ace"
    }
    & sc.exe sdset $serviceName $updated | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to grant service control to $Account." }
    Write-Log "Granted $Account limited control of $serviceName."
}

Set-Location $root
Set-Content -LiteralPath $logPath -Encoding UTF8 -Value "PunchBotService one-time setup log"
Assert-Admin

if (-not (Test-Path -LiteralPath $python)) { throw "Missing virtual environment Python: $python" }
if (-not (Test-Path -LiteralPath $nssm)) { throw "Missing NSSM: $nssm" }

& $python -m py_compile "bot_all_in_one.py"
if ($LASTEXITCODE -ne 0) { throw "Python syntax check failed." }
Write-Log "Python syntax check passed."

$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing -and $existing.Status -ne "Stopped") {
    Stop-Service -Name $serviceName -Force
    Start-Sleep -Seconds 3
}
if (-not $existing) {
    & $nssm install $serviceName $python "bot_all_in_one.py" | ForEach-Object { Write-Log $_ }
    if ($LASTEXITCODE -ne 0) { throw "NSSM service installation failed." }
}

& $nssm set $serviceName Application $python | Out-Null
& $nssm set $serviceName AppParameters "bot_all_in_one.py" | Out-Null
& $nssm set $serviceName AppDirectory $root | Out-Null
& $nssm set $serviceName DisplayName "Punch Relay Discord Bot" | Out-Null
& $nssm set $serviceName Description "Runs Punch Relay with automatic recovery and health checks." | Out-Null
& $nssm set $serviceName Start SERVICE_AUTO_START | Out-Null
& $nssm set $serviceName AppExit Default Restart | Out-Null
& $nssm set $serviceName AppRestartDelay 10000 | Out-Null
& $nssm set $serviceName AppThrottle 10000 | Out-Null
& $nssm set $serviceName AppStopMethodConsole 3000 | Out-Null
& $nssm set $serviceName AppRotateFiles 1 | Out-Null
& $nssm set $serviceName AppRotateOnline 1 | Out-Null
& $nssm set $serviceName AppRotateBytes 1048576 | Out-Null

Grant-ServiceControl $ControlUser
Start-Service -Name $serviceName
Start-Sleep -Seconds 10
$svc = Get-Service -Name $serviceName
Write-Log "Service status: $($svc.Status); startup: $($svc.StartType)"

if (Test-Path (Join-Path $root "bot.log")) {
    Get-Content (Join-Path $root "bot.log") -Encoding UTF8 -Tail 25
}
if (-not $NoPause) { Read-Host "Press Enter to close" }
