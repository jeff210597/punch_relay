param(
    [int]$DebounceSeconds = 45,
    [int]$PollSeconds = 5
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$SyncScript = Join-Path $Root "sync_to_github.ps1"
$LogPath = Join-Path $Root "github_sync_watcher.log"
Set-Location $Root

function Write-WatcherLog {
    param([string]$Message)
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message$([Environment]::NewLine)"
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            [IO.File]::AppendAllText($LogPath, $line, [Text.UTF8Encoding]::new($false))
            return
        }
        catch {
            if ($attempt -eq 3) { throw }
            Start-Sleep -Milliseconds 250
        }
    }
}

function Find-Git {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) { return $git.Source }

    $parent = Split-Path -Parent $Root
    $documents = Split-Path -Parent $parent
    $candidates = @(
        (Join-Path $Root "PortableGit\mingw64\bin\git.exe"),
        (Join-Path $parent "PortableGit\mingw64\bin\git.exe"),
        (Join-Path $parent "work\PortableGit\mingw64\bin\git.exe"),
        (Join-Path $documents "work\PortableGit\mingw64\bin\git.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "git.exe not found. Install Git for Windows or add git.exe to PATH."
}

function Get-RepoSignature {
    $status = & $GitExe status --porcelain=v1 --untracked-files=normal
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed with exit code $LASTEXITCODE"
    }
    return (($status | Sort-Object) -join "`n")
}

if (-not (Test-Path $SyncScript)) {
    throw "Missing sync script: $SyncScript"
}
if ($DebounceSeconds -lt 1 -or $PollSeconds -lt 1) {
    throw "DebounceSeconds and PollSeconds must both be at least 1."
}

$GitExe = Find-Git
Write-WatcherLog "watcher starting at $Root (poll=${PollSeconds}s, debounce=${DebounceSeconds}s)"

$lastSignature = ""
$stableSince = $null

while ($true) {
    try {
        $signature = Get-RepoSignature
        if (-not $signature) {
            $lastSignature = ""
            $stableSince = $null
        }
        elseif ($signature -ne $lastSignature) {
            $lastSignature = $signature
            $stableSince = Get-Date
            Write-WatcherLog "change detected: repository status changed"
        }
        elseif ($stableSince -and ((Get-Date) - $stableSince).TotalSeconds -ge $DebounceSeconds) {
            Write-WatcherLog "debounce elapsed; running sync"
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SyncScript
            if ($LASTEXITCODE -ne 0) {
                throw "sync script exited with code $LASTEXITCODE"
            }
            Write-WatcherLog "sync script finished"
            $lastSignature = ""
            $stableSince = $null
        }
    }
    catch {
        Write-WatcherLog "sync failed: $($_.Exception.Message)"
        $stableSince = Get-Date
    }

    Start-Sleep -Seconds $PollSeconds
}
