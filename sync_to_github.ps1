param(
    [switch]$DryRun,
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""
$env:GIT_HTTP_PROXY = ""
$env:GIT_HTTPS_PROXY = ""
$env:GIT_TERMINAL_PROMPT = "0"

function Write-SyncLog {
    param([string]$Message)
    $logPath = Join-Path $Root "github_sync.log"
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message$([Environment]::NewLine)"
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            [IO.File]::AppendAllText($logPath, $line, [Text.UTF8Encoding]::new($false))
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
    if ($git) {
        return $git.Source
    }

    $parent = Split-Path -Parent $Root
    $documents = Split-Path -Parent $parent
    $candidates = @(
        (Join-Path $Root "PortableGit\mingw64\bin\git.exe"),
        (Join-Path $parent "PortableGit\mingw64\bin\git.exe"),
        (Join-Path $parent "work\PortableGit\mingw64\bin\git.exe"),
        (Join-Path $documents "work\PortableGit\mingw64\bin\git.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "git.exe not found. Install Git for Windows or add git.exe to PATH."
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    & $GitExe -c "safe.directory=$Root" -c http.sslBackend=openssl @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-GitRemote {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    if ($env:GITHUB_PAT) {
        $pair = "x-access-token:$($env:GITHUB_PAT)"
        $basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
        & $GitExe -c "safe.directory=$Root" -c http.sslBackend=openssl -c credential.helper= -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $basic" @GitArgs
    }
    else {
        Write-SyncLog "GITHUB_PAT is not set; running non-interactive remote command without credential helpers"
        & $GitExe -c "safe.directory=$Root" -c http.sslBackend=openssl -c credential.helper= @GitArgs
    }

    if ($LASTEXITCODE -ne 0) {
        if (-not $env:GITHUB_PAT) {
            throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE. No GITHUB_PAT was set, so the script refused interactive credential prompts. Install PunchRelayGitSync with -GitHubPat or complete Git credential setup manually before retrying."
        }
        throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Assert-NoForbiddenStagedFiles {
    $forbidden = @(
        '^\.env($|\.)',
        '^punch_data\.json$',
        '^punched_today\.json$',
        '^schedule_today\.json$',
        '^admin_alerts_today\.json$',
        '^synced\.flag$',
        '^bot\.log$',
        '^github_sync\.log$',
        '^github_sync_watcher\.log$',
        '^github_sync_service\.log$',
        '\.bak$',
        '^__pycache__/',
        '^\.codex-remote-attachments/'
    )

    $staged = & $GitExe -c "safe.directory=$Root" diff --cached --name-only
    foreach ($file in $staged) {
        foreach ($pattern in $forbidden) {
            if ($file -match $pattern) {
                Invoke-Git reset -q -- $file
                throw "Refused to sync forbidden file: $file"
            }
        }
    }
}

function Assert-NoObviousSecrets {
    $diff = & $GitExe -c "safe.directory=$Root" diff --cached --unified=0
    $scanText = ($diff -split "`n" | Where-Object {
        $_.StartsWith("+") -and
        -not $_.StartsWith("+++") -and
        $_ -notmatch 'secretPatterns|DISCORD_TOKEN\\s|github_pat_\[|password\\s'
    }) -join "`n"

    $secretPatterns = @(
        'DISCORD_TOKEN\s*=\s*(?!your|REPLACE|example|placeholder).{20,}',
        'github_pat_[A-Za-z0-9_]{20,}',
        'password\s*=\s*["''][^"'']{4,}["'']'
    )

    foreach ($pattern in $secretPatterns) {
        if ($scanText -match $pattern) {
            throw "Refused to sync because staged diff looks like it contains a secret."
        }
    }
}

$GitExe = Find-Git
$mutex = [Threading.Mutex]::new($false, "Global\PunchRelayGitSync")
$hasMutex = $false

try {
    $hasMutex = $mutex.WaitOne(0)
    if (-not $hasMutex) {
        Write-SyncLog "another sync is already running; skipping"
        exit 0
    }

    Write-SyncLog "sync check started at $Root"

    $inside = & $GitExe -c "safe.directory=$Root" rev-parse --is-inside-work-tree
    if ($LASTEXITCODE -ne 0 -or $inside.Trim() -ne "true") {
        throw "$Root is not a git repository."
    }

    $branch = (& $GitExe -c "safe.directory=$Root" branch --show-current).Trim()
    if ($branch -ne "main") {
        throw "Refused to auto-sync branch '$branch'. Expected 'main'."
    }

    Invoke-GitRemote fetch origin main
    $counts = ((& $GitExe -c "safe.directory=$Root" rev-list --left-right --count main...origin/main).Trim() -split '\s+')
    if ($LASTEXITCODE -ne 0 -or $counts.Count -lt 2) {
        throw "Unable to compare main with origin/main."
    }
    $ahead = [int]$counts[0]
    $behind = [int]$counts[1]
    if ($behind -gt 0) {
        throw "Refused to auto-sync because local main is behind origin/main by $behind commit(s). Update the checkout before retrying."
    }

    $porcelain = & $GitExe -c "safe.directory=$Root" status --porcelain
    if ($porcelain) {
        Invoke-Git add -A -- .
        Assert-NoForbiddenStagedFiles
        Assert-NoObviousSecrets

        $stagedNames = & $GitExe -c "safe.directory=$Root" diff --cached --name-only
        if (-not $stagedNames) {
            Write-SyncLog "changes exist, but nothing safe is staged"
            exit 0
        }

        Write-SyncLog "staged files: $($stagedNames -join ', ')"

        if ($DryRun) {
            Invoke-Git reset -q
            Write-SyncLog "dry run complete; no commit or push"
            exit 0
        }

        $message = "Auto-sync punch relay changes $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        Invoke-Git commit -m $message
        $ahead++
    }

    if ($ahead -eq 0) {
        Write-SyncLog "no local changes"
        exit 0
    }

    if ($NoPush) {
        Write-SyncLog "local commit exists; push skipped by -NoPush"
        exit 0
    }

    Invoke-GitRemote push origin main
    Invoke-GitRemote fetch origin main
    $localSha = (& $GitExe -c "safe.directory=$Root" rev-parse HEAD).Trim()
    $remoteSha = (& $GitExe -c "safe.directory=$Root" rev-parse origin/main).Trim()
    if ($localSha -ne $remoteSha) {
        throw "Remote verification failed: local HEAD does not match origin/main."
    }
    Write-SyncLog "push complete and remote verified at $localSha"
}
finally {
    if ($hasMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
