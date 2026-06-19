$GitHubPat = $null
if ($args.Count -ge 2 -and $args[0] -eq "-GitHubPat") {
    $GitHubPat = [string]$args[1]
}

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServiceName = "PunchRelayGitSync"
$DisplayName = "Punch Relay GitHub Auto Sync"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$WatcherScript = Join-Path $Root "watch_github_sync.ps1"

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Please run this script as Administrator."
    }
}

function Get-NssmPath {
    $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
    $path = Join-Path $Root "tools\nssm\$arch\nssm.exe"
    if (-not (Test-Path $path)) {
        throw "Missing NSSM executable: $path"
    }
    return $path
}

Assert-Admin

if (-not (Test-Path $WatcherScript)) {
    throw "Missing watcher script: $WatcherScript"
}

$Nssm = Get-NssmPath
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

if (-not $service) {
    & $Nssm install $ServiceName $PowerShellExe "-NoProfile -ExecutionPolicy Bypass -File `"$WatcherScript`""
    if ($LASTEXITCODE -ne 0) {
        throw "NSSM install failed with exit code $LASTEXITCODE"
    }
}
else {
    Stop-Service -Name $ServiceName -ErrorAction SilentlyContinue
}

& $Nssm set $ServiceName DisplayName $DisplayName | Out-Null
& $Nssm set $ServiceName AppDirectory $Root | Out-Null
& $Nssm set $ServiceName AppStdout (Join-Path $Root "github_sync_service.log") | Out-Null
& $Nssm set $ServiceName AppStderr (Join-Path $Root "github_sync_service.log") | Out-Null
& $Nssm set $ServiceName AppRotateFiles 1 | Out-Null
& $Nssm set $ServiceName AppRotateOnline 1 | Out-Null
& $Nssm set $ServiceName AppRotateBytes 1048576 | Out-Null
& $Nssm set $ServiceName Start SERVICE_AUTO_START | Out-Null
if ($GitHubPat) {
    & $Nssm set $ServiceName AppEnvironmentExtra "GITHUB_PAT=$GitHubPat" | Out-Null
}

Start-Service -Name $ServiceName
Get-Service -Name $ServiceName
