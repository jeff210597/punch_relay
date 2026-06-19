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
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$stamp] $Message" | Tee-Object -FilePath (Join-Path $Root "github_sync.log") -Append
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
    & $GitExe -c http.sslBackend=openssl @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-GitPush {
    if ($env:GITHUB_PAT) {
        $pair = "x-access-token:$($env:GITHUB_PAT)"
        $basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
        & $GitExe -c http.sslBackend=openssl -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $basic" push origin main
    }
    else {
        & $GitExe -c http.sslBackend=openssl push origin main
    }

    if ($LASTEXITCODE -ne 0) {
        throw "git push origin main failed with exit code $LASTEXITCODE"
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
        '\.bak$',
        '^__pycache__/',
        '^\.codex-remote-attachments/'
    )

    $staged = & $GitExe diff --cached --name-only
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
    $diff = & $GitExe diff --cached --unified=0
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
Write-SyncLog "sync check started at $Root"

$inside = & $GitExe rev-parse --is-inside-work-tree
if ($LASTEXITCODE -ne 0 -or $inside.Trim() -ne "true") {
    throw "$Root is not a git repository."
}

$branch = (& $GitExe branch --show-current).Trim()
if ($branch -ne "main") {
    throw "Refused to auto-sync branch '$branch'. Expected 'main'."
}

$porcelain = & $GitExe status --porcelain
if (-not $porcelain) {
    Write-SyncLog "no local changes"
    exit 0
}

Invoke-Git add -A -- .
Assert-NoForbiddenStagedFiles
Assert-NoObviousSecrets

$stagedNames = & $GitExe diff --cached --name-only
if (-not $stagedNames) {
    Write-SyncLog "changes exist, but nothing safe is staged"
    exit 0
}

Write-SyncLog "staged files: $($stagedNames -join ', ')"

if ($DryRun) {
    Write-SyncLog "dry run complete; no commit or push"
    exit 0
}

$message = "Auto-sync punch relay changes $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Invoke-Git commit -m $message

if ($NoPush) {
    Write-SyncLog "commit created; push skipped by -NoPush"
    exit 0
}

Invoke-GitPush
Write-SyncLog "push complete"
