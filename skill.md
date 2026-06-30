---
name: punch-relay
description: Work on the Windows Discord punch bot in C:\Users\7c\Documents\punch_relay. Use this for bot edits, B9/e-HR punch parsing, slash command sync, live service validation, host migration, and production safety checks.
---

# Punch Relay Skill

## Core Runtime

- Production runs through NSSM as `PunchBotService`.
- The service runs `.venv\Scripts\python.exe bot_all_in_one.py` from the repo root.
- Daily service control should use the repo scripts, not process-name mass kills.
- Only first-time service install, service reconfiguration, or removal should need an elevated PowerShell.
- Keep GitHub auto-watch, auto-commit, and auto-push disabled unless the user explicitly asks for them.

## Edit And Verify Flow

Use this chain after bot code, restart script, lifecycle, schedule, or status changes:

```powershell
.\.venv\Scripts\python.exe -m py_compile .\bot_all_in_one.py
powershell -ExecutionPolicy Bypass -File .\bot_status.ps1
powershell -ExecutionPolicy Bypass -File .\restart_bot.ps1 -NoPause
```

- If slash command definitions changed, use the resync restart instead:

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_resync.ps1 -NoPause
```

- Confirm `PunchBotService` is `Running` and `Automatic`.
- Inspect fresh runtime evidence after restart, not stale log lines.
- Prefer the newest `bot.log` region and current `bot_runtime_state.json`.
- If user-facing status text changes, check every path that can render it, especially `today_status()` and `get_today_schedule()`.

## Slash Command Sync

- Use `restart_bot_resync.ps1 -NoPause` after adding, renaming, removing, or changing slash command permissions.
- `synced.flag` can prevent Discord from seeing command changes. The resync script should clear or bypass it.
- For admin commands, current product rule is: Discord server Administrator permission can see and use admin slash commands; non-admin users should not see those commands in Discord.
- Prefer improving the existing command over creating duplicate aliases.

## B9 e-HR Reading Rules

- Treat `_parse_monthly_records()`, `_query_today_from_monthly_b9()`, `infer_punch_times()`, and `_query_monthly_summary()` as linked behavior.
- If one B9 path changes, inspect all render and comparison paths that consume `clock_in`, `clock_out`, `clock_out_source`, `raw_times`, or `makeup_times`.
- B9 row fields are interpreted as `date`, `shift`, `in`, `out`, `abnormal`, `leave_text`, `raw_times`, and `makeup_times`.
- Preserve `shift` and `abnormal`; do not parse only the visible in/out time cells.
- Parse B9 time cells by extracting the first four-digit time.
- A value like `0805(next-day marker)` means `08:05` with an `out_next_day=True` marker. Do not discard it because the cell has extra text.
- For a duty day, a valid duty-out source can be either:
  - the same duty-day B9 row's out cell marked as next-day, such as `0805(next-day marker)`;
  - the next day's B9 out cell when it falls inside the duty-out range `08:05~08:40`.
- When querying the duty-after day, look back to the previous B9 row. If the previous row has `out_next_day=True`, return that time as the current day's duty-out record with `clock_out_source="ehr_next_day"`.
- Display `ehr_next_day` as an e-HR-confirmed value, not as a pending or raw-time fallback.
- A duty-after day should not be counted again as missing normal in/out cards when its duty-out was already credited to the previous duty day.

## Monthly Summary Rules

- Expected work comes from local bot settings and duty logic, not from a broad holiday classifier.
- Local leave days and weekend non-duty days are non-work days.
- If a non-work day has accidental B9 in/out punches, ignore those punches for normal, abnormal, and missing-card counts.
- Do not add a separate official-holiday classification unless the user explicitly asks for it.
- Normal workdays, duty days, and duty-after requirements still need missing-card validation.
- Monthly summary delivery is scheduled at 19:00 local time.
- Track sent monthly summaries with `monthly_summary_sent.json` to avoid duplicates when that state matters.

## B9 Validation Fixtures

Use small synthetic B9 HTML fixtures when changing B9 logic:

- Supervisor-uploaded duty: duty day row has `in=0708`, `out=0805(next-day marker)`, next day has no out. Expected: next-day query returns `clock_out=08:05`, `clock_out_source=ehr_next_day`, and the monthly summary counts the duty day as normal.
- Duty not uploaded yet: duty day row has `in=0708`, next day row has `out=0805`. Expected: summary credits `08:05` back to the duty day when local `duty_days` marks the previous date as duty.
- Non-work accidental punch: a local leave day or weekend non-duty day has a punch such as `1932`. Expected: summary ignores it for normal, abnormal, and missing counts.
- Missing expected work: a normal workday, duty day, or duty-after requirement lacks the required card. Expected: summary counts it as abnormal or missing.

## Host Migration Checklist

Before moving to another host:

- Copy or recreate `.env` with `DISCORD_TOKEN`, `NOTIFY_CHANNEL_ID`, `ADMIN_ALERT_CHANNEL_ID`, and `EHR_BASE`.
- Copy business/runtime state that must persist, especially `punch_data.json`.
- Copy `monthly_summary_sent.json` if avoiding duplicate monthly summary delivery matters.
- If migrating during the same day, decide whether to copy `schedule_today.json`, `punched_today.json`, and `admin_alerts_today.json`.
- Copying same-day runtime JSON preserves random schedules, already-punched keys, and sent-alert state.
- Omitting same-day runtime JSON lets the bot regenerate state and can cause duplicate or missed same-day behavior if the migration is poorly timed.
- Treat `bot_runtime_state.json` as operational evidence, not authoritative business data. It can be regenerated, but inspect it after first startup.
- Prefer not to carry an old `synced.flag` to a new host. Run a resync restart on first startup.

On the new host:

- Keep the repo path as `C:\Users\7c\Documents\punch_relay` when possible. If the path changes, verify NSSM `Application`, `AppParameters`, and `AppDirectory`.
- Run `setup_python_env.ps1` and use `.venv\Scripts\python.exe`, not a random system Python.
- Run `install_nssm_service_admin.ps1` once from an elevated PowerShell to install or reconfigure `PunchBotService`.
- Confirm the Windows service account can read and write the repo, `.env`, runtime JSON files, and logs.
- Confirm Windows timezone and local clock are correct for Asia/Taipei behavior.
- Confirm e-HR/B9 network access and `EHR_BASE` from the new host.
- After first start, run `bot_status.ps1`, inspect fresh `bot.log`, and verify `bot_runtime_state.json` updated.

## Security And Git Rules

- Never print, upload, or commit `.env`, Discord token, passwords, cookies, employee data, runtime JSON with user data, logs, backups, `.venv`, or `.python`.
- Do not commit `synced.flag`; it is host/runtime state.
- Check `git diff` before publishing so unrelated user changes are not reverted or mixed in accidentally.
- If publishing to GitHub is requested, verify the actual branch and remote first. `gh` availability can differ by host.

## Common Pitfalls

- `restart_bot.ps1` or `restart_bot_resync.ps1` blocked by policy: rerun with `powershell -ExecutionPolicy Bypass -File ...`.
- Runtime JSON parse fails with an unexpected UTF-8 BOM: PowerShell 5 may have written BOM; read with `utf-8-sig` or rewrite without BOM.
- Restart output looks clean but behavior is stale: inspect the newest log/runtime region after restart.
- A status fix works in one command but not another: grep and patch all status-producing paths.
- Cross-month duty logic can break if monthly automation rules override duty-after precedence. Always validate a case like `6/30` duty to `7/1` duty-after.
