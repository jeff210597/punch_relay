# GitHub Upload Guide

Use this checklist before pushing changes to `jeff210597/punch_relay`.

## Never Commit

- `.env`
- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `bot.log`
- `synced.flag`
- `*.bak`
- `__pycache__/`

These files contain secrets, runtime state, user data, or local cache.

## Expected Repo Files

- `bot_all_in_one.py`
- `requirements.txt`
- `.env.example`
- `README.md`
- `.gitignore`
- `start.bat`
- `install_nssm_service_admin.ps1`
- `restart_bot_admin.ps1`
- `restart_bot_resync_admin.ps1`
- `tools/nssm/win32/nssm.exe`
- `tools/nssm/win64/nssm.exe`
- `agent.md`
- `skill.md`
- `docs/`

## Validation

Run from the repository root:

```powershell
python -m py_compile bot_all_in_one.py
git status
git diff
```

Search for accidental secrets:

```powershell
rg -n "DISCORD_TOKEN|EHR_BASE|pwd|password|token|密碼" README.md docs bot_all_in_one.py restart_bot_admin.ps1 restart_bot_resync_admin.ps1 .env.example agent.md skill.md
```

The search may show variable names and examples. It must not show real secrets.

## Push

```powershell
git add <changed-files>
git commit -m "Describe the change"
git push origin main
```

Use explicit file paths when staging so local runtime files are not accidentally committed.
