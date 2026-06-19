---
name: punch-procedure
description: Repository-local operating rules for maintaining and deploying the punch_relay Discord bot.
---

# Punch Relay Skill

Use this skill whenever working on the `punch_relay` bot, its Windows service scripts, deployment files, GitHub sync, or runtime verification.

## Core Rule

After any change under a punch relay project folder, sync the intended repository changes to:

```text
https://github.com/jeff210597/punch_relay
```

Do not leave deployable code, service scripts, docs, or bundled tool changes only on the host. Runtime state and secrets must stay local.

Keep the Windows auto-sync service installed and running on maintained hosts unless the user explicitly disables automatic upload:

```text
PunchRelayGitSync
```

## Never Commit

Never stage or upload:

- `.env`
- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `bot.log`
- `github_sync.log`
- `github_sync_watcher.log`
- `github_sync_service.log`
- `synced.flag`
- `*.bak`
- `__pycache__/`
- `.codex-remote-attachments/`

Before any commit or GitHub API update, scan for real secrets:

```powershell
rg -n "DISCORD_TOKEN|EHR_BASE|pwd|password|token|密碼" README.md docs bot_all_in_one.py restart_bot_admin.ps1 restart_bot_resync_admin.ps1 .env.example agent.md skill.md sync_to_github.ps1 watch_github_sync.ps1 install_github_sync_watcher_admin.ps1
```

Variable names and placeholders are allowed. Real Discord tokens, GitHub tokens, e-HR URLs, passwords, cookies, and user data are not allowed.

## Required Validation

For code changes, run:

```powershell
python -m py_compile bot_all_in_one.py
```

For deployment/script changes, also verify:

```powershell
Test-Path tools\nssm\win32\nssm.exe
Test-Path tools\nssm\win64\nssm.exe
rg -n "C:\\punch_relay|C:\\Users\\7b\\Documents\\punch_relay" README.md docs agent.md skill.md bot_all_in_one.py start.bat restart_bot_admin.ps1 restart_bot_resync_admin.ps1 install_nssm_service_admin.ps1 sync_to_github.ps1 watch_github_sync.ps1 install_github_sync_watcher_admin.ps1
```

The path scan should return no fixed project-root dependencies. Scripts should use their own location as the project root.

## GitHub Sync Procedure

1. Prefer the built-in sync script for routine host changes:

```powershell
powershell -ExecutionPolicy Bypass -File .\sync_to_github.ps1
```

2. Inspect local changes manually when the sync script fails or before larger edits:

```powershell
git status --short --branch
git diff
```

3. Stage only intended repository files. Prefer explicit paths. Do not use broad staging when runtime files are present.

4. Commit with a clear message.

5. Push to `main`:

```powershell
git push origin main
```

## Auto Sync Service

Install the watcher from an Administrator PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_github_sync_watcher_admin.ps1
```

The watcher runs `watch_github_sync.ps1`, ignores local runtime and secret files, waits 45 seconds after changes settle, then runs `sync_to_github.ps1`. The sync script refuses to stage `.env`, runtime JSON, logs, backup files, obvious Discord/GitHub tokens, or password-like staged content. Keep watcher logs separate from NSSM stdout/stderr logs so the service does not lock its own log file.

## Stable GitHub Authentication Policy

Do not repeatedly retry `git push` when authentication is missing. It wastes time and tokens.

Use this sequence:

1. Try one normal `git push origin main`.

2. If it hangs or times out, run one non-interactive diagnostic:

```powershell
$env:GIT_TERMINAL_PROMPT='0'
git -c credential.helper= push origin main
```

3. If the diagnostic says it cannot read the GitHub username, stop retrying local push.

4. Prefer a stable credential setup before continuing:

```powershell
git config --global credential.helper manager
git push origin main
```

Complete the GitHub browser/device login once when prompted. After that, future pushes should reuse the stored credential.

5. If a user provides a GitHub PAT, use it only for GitHub credential setup or a single env-only push. Never write PATs to repository files, logs, remotes, scripts, or documentation.

6. If the host cannot complete GitHub credential setup and GitHub connector tools are available, use the connector/API path for small text-only updates. For large or many-file syncs, ask for one-time GitHub authentication rather than manually reconstructing large files through the API.

7. Never force-push, reset, or rebase unless explicitly requested.

## Deployment Expectations

The repository should be enough to rebuild a new host:

1. Clone the repo.
2. Install Python and `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and fill secrets locally.
4. Run `python -m py_compile bot_all_in_one.py`.
5. Run `install_nssm_service_admin.ps1` as Administrator.
6. Use `restart_bot_resync_admin.ps1` when slash commands changed.
7. Run `install_github_sync_watcher_admin.ps1` as Administrator to enable automatic GitHub sync.

The repo includes both NSSM binaries:

- `tools/nssm/win32/nssm.exe`
- `tools/nssm/win64/nssm.exe`

Do not require a fixed install directory such as `C:\punch_relay`.
