$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$SyncScript = Join-Path $Root "sync_to_github.ps1"
$LogPath = Join-Path $Root "github_sync.log"
$DebounceSeconds = 45

function Write-WatcherLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$stamp] $Message" | Tee-Object -FilePath $LogPath -Append
}

function Test-IgnoredPath {
    param([string]$Path)

    $relative = $Path
    if ($Path.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        $relative = $Path.Substring($Root.Length).TrimStart('\', '/')
    }

    return (
        $relative -match '(^|[\\/])\.git([\\/]|$)' -or
        $relative -match '(^|[\\/])__pycache__([\\/]|$)' -or
        $relative -match '(^|[\\/])\.codex-remote-attachments([\\/]|$)' -or
        $relative -match '(^|[\\/])\.env($|\.)' -or
        $relative -match '\.log$' -or
        $relative -match '\.bak$' -or
        $relative -match '(^|[\\/])(punch_data|punched_today|schedule_today|admin_alerts_today)\.json$' -or
        $relative -eq 'synced.flag'
    )
}

if (-not (Test-Path $SyncScript)) {
    throw "Missing sync script: $SyncScript"
}

Write-WatcherLog "watcher starting at $Root"

$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $Root
$watcher.IncludeSubdirectories = $true
$watcher.EnableRaisingEvents = $true
$watcher.NotifyFilter = [System.IO.NotifyFilters]'FileName, DirectoryName, LastWrite, Size'

$script:LastEvent = Get-Date
$script:Pending = $false

$action = {
    if (Test-IgnoredPath -Path $Event.SourceEventArgs.FullPath) {
        return
    }
    $script:LastEvent = Get-Date
    $script:Pending = $true
    Write-WatcherLog "change detected: $($Event.SourceEventArgs.ChangeType) $($Event.SourceEventArgs.FullPath)"
}

$registrations = @(
    Register-ObjectEvent $watcher Changed -Action $action,
    Register-ObjectEvent $watcher Created -Action $action,
    Register-ObjectEvent $watcher Deleted -Action $action,
    Register-ObjectEvent $watcher Renamed -Action $action
)

try {
    while ($true) {
        Start-Sleep -Seconds 5
        if ($script:Pending -and ((Get-Date) - $script:LastEvent).TotalSeconds -ge $DebounceSeconds) {
            $script:Pending = $false
            Write-WatcherLog "debounce elapsed; running sync"
            try {
                powershell -NoProfile -ExecutionPolicy Bypass -File $SyncScript
                Write-WatcherLog "sync script finished"
            }
            catch {
                Write-WatcherLog "sync failed: $($_.Exception.Message)"
            }
        }
    }
}
finally {
    foreach ($registration in $registrations) {
        Unregister-Event -SubscriptionId $registration.Id -ErrorAction SilentlyContinue
    }
    $watcher.Dispose()
}
