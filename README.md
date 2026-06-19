# Punch Relay Discord Bot

This repository contains the Discord punch bot, Windows service scripts, bundled NSSM binaries, deployment notes, and GitHub auto-sync scripts. A new Windows host should be able to reproduce the bot by cloning this repository, installing Python dependencies, creating a local `.env`, and installing the services.

## Files

- `bot_all_in_one.py`: main Discord bot.
- `requirements.txt`: Python dependencies.
- `.env.example`: local environment template. Copy it to `.env` and fill real values locally.
- `tools/nssm/win32/nssm.exe` and `tools/nssm/win64/nssm.exe`: bundled NSSM binaries.
- `install_nssm_service_admin.ps1`: installs or updates `PunchBotService`.
- `restart_bot_admin.ps1`: restarts the bot service.
- `restart_bot_resync_admin.ps1`: removes `synced.flag` and restarts the bot so slash commands sync again.
- `sync_to_github.ps1`: commits and pushes safe repository changes.
- `watch_github_sync.ps1`: watches the folder and runs the sync script after changes settle.
- `install_github_sync_watcher_admin.ps1`: installs `PunchRelayGitSync`, the watcher Windows service.
- `agent.md`, `skill.md`, and `docs/`: maintenance notes.

## Local Files That Must Not Be Uploaded

The following files contain secrets, personal data, runtime state, or logs and are ignored by Git:

- `.env`
- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `synced.flag`
- `bot.log`
- `github_sync.log`
- `github_sync_watcher.log`
- `github_sync_service.log`
- `*.bak`
- `__pycache__/`

Never upload Discord tokens, GitHub tokens, e-HR passwords, cookies, personal punch data, or logs.

## New Host Setup

1. Clone the repository:

```powershell
git clone https://github.com/jeff210597/punch_relay.git
cd punch_relay
```

2. Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Create `.env`:

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill the local values:

```text
DISCORD_TOKEN=your Discord bot token
NOTIFY_CHANNEL_ID=Discord notify channel ID
ADMIN_ALERT_CHANNEL_ID=Discord admin alert channel ID
EHR_BASE=http://your-ehr-host
```

Use `EHR_BASE` without a trailing slash.

4. Validate syntax:

```powershell
python -m py_compile bot_all_in_one.py
```

5. Install or update the bot service from an Administrator PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_nssm_service_admin.ps1
```

6. Restart and force slash command sync after command changes:

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_resync_admin.ps1
```

For a normal restart:

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_admin.ps1
```

## GitHub Auto Sync

The PAT only proves push permission. Folder change detection is handled by a separate watcher service.

1. Make sure GitHub HTTPS authentication works once on the host. Prefer Git Credential Manager for interactive use. For the Windows service, pass a PAT during installation so LocalSystem can push without an interactive login. Do not store the PAT inside this repository.

2. Test a safe sync manually:

```powershell
powershell -ExecutionPolicy Bypass -File .\sync_to_github.ps1 -DryRun
```

3. Install the watcher from an Administrator PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_github_sync_watcher_admin.ps1
```

The service name is `PunchRelayGitSync`. It watches the repository folder and runs `sync_to_github.ps1` after changes settle for 45 seconds.

To give the service push permission, run the installer with a local PAT value:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_github_sync_watcher_admin.ps1 -GitHubPat "your PAT"
```

This stores the PAT in the local Windows service environment, not in Git or repository files.

Check it with:

```powershell
Get-Service PunchRelayGitSync
Get-Content .\github_sync_watcher.log -Tail 50
Get-Content .\github_sync.log -Tail 50
```

## Verification

```powershell
Get-Service PunchBotService
Get-Content .\bot.log -Tail 50 -Encoding UTF8
python -m py_compile bot_all_in_one.py
git status
```

Before each commit or push, confirm no `.env`, runtime JSON, logs, tokens, passwords, or backup files are staged.
