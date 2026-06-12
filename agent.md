# Punch Relay Agent Guide

## Role

This file is for agents maintaining this repository. It defines how to change, verify, restart, and publish the punch bot. It does not define punch business rules in detail; use `skill.md` for punch procedure reasoning.

Primary duties:

- Protect Discord tokens, e-HR credentials, employee data, logs, and local state.
- Keep punch behavior conservative: no duplicate punches, no missed eligible punches, no automatic/makeup `in` punch at or after 08:00.
- After every code, setting, or documentation change: validate, restart if needed, inspect logs, commit, and push to GitHub.

## Project Files

- `bot_all_in_one.py`: main Discord bot.
- `agent.md`: repository maintenance rules for agents.
- `skill.md`: punch procedure skill for behavior decisions.
- `.env.example`: safe environment variable example.
- `.env`: local secret config, never commit.
- `punch_data.json`: bound user data, never commit.
- `punched_today.json`: daily successful punch keys, never commit.
- `schedule_today.json`: daily random schedule, never commit.
- `admin_alerts_today.json`: daily admin alert de-duplication, never commit.
- `bot.log`: runtime log, never commit.

## Current Runtime Settings

Environment variables:

- `DISCORD_TOKEN`: required Discord bot token.
- `NOTIFY_CHANNEL_ID`: required fallback notification channel.
- `ADMIN_ALERT_CHANNEL_ID`: admin alert channel, currently `1514929880448630904` (`admin-alert`).
- `EHR_BASE`: required e-HR base URL.

Punch windows:

- `in`: 07:00-07:40
- normal `out`: 17:05-17:40
- `dutyout`: 08:05-08:40 on the day after duty

Retry and makeup:

- Automatic retry delay: 2 minutes.
- Maximum automatic retries: 3.
- Makeup button timeout: 10 minutes.
- `in` retry/makeup cutoff: strictly before 08:00.

## Agent vs Skill

Agent responsibilities:

- Read code and local docs.
- Modify files in this repo.
- Run validation.
- Restart `PunchBotService` when needed.
- Check logs.
- Scan for secrets.
- Commit and push to GitHub.

Skill responsibilities:

- Decide whether a punch should happen.
- Explain retry, makeup, e-HR comparison, leave, duty, weekend, cancellation, and admin alert behavior.
- Prevent business-rule mistakes while changing punch logic.

Avoid duplicating detailed punch behavior here. Keep operational punch reasoning in `skill.md`.

## Change Rules

1. Before modifying runtime behavior, check whether the current time is near an active punch window.
2. Prefer small patches. Do not do unrelated refactors.
3. Preserve backward compatibility for `punch_data.json`; add defaults through `get_user_data()`.
4. Write to `punched_today.json` only after confirmed success from the bot's perspective.
5. Never allow automatic retry or makeup button confirmation to call `punch_clock(..., "in")` at or after 08:00.
6. Keep long HTTP work off the Discord event loop.
7. Do not print or document tokens, passwords, cookies, raw e-HR payloads, or full sensitive URLs.
8. If slash commands are added, removed, or renamed, restart with the resync script.

## Validation

Always run:

```powershell
python -m py_compile bot_all_in_one.py
```

For punch behavior changes, also verify:

- `punched_today.json` keys prevent duplicate punches.
- `schedule_today.json` preserves same-day random times.
- `retry_queue` retries after 2 minutes and stops after 3 tries.
- `in` makeup/retry has an 08:00 cutoff at scheduling time, retry execution time, and button confirmation time.
- Admin alerts go to `admin-alert`, not `#一般`.

Before GitHub upload, scan for sensitive material:

```powershell
rg -n "DISCORD_TOKEN|EHR_BASE|pwd|password|token|密碼" README.md docs bot_all_in_one.py restart_bot_admin.ps1 restart_bot_resync_admin.ps1 .env.example agent.md skill.md
```

Variable names and safe examples are acceptable. Real tokens, passwords, employee IDs, internal URLs, cookies, or logs are not.

## Restart

Normal restart:

```powershell
Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File C:\punch_relay\restart_bot_admin.ps1 -NoPause" -Verb RunAs -WindowStyle Hidden
```

Restart with slash command resync:

```powershell
Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File C:\punch_relay\restart_bot_resync_admin.ps1 -NoPause" -Verb RunAs -WindowStyle Hidden
```

After restart, confirm:

- `PunchBotService` is `Running`.
- `bot.log` shows bot startup.
- `auto_punch_task` started.

## GitHub Rule

Every completed change to code, settings examples, documentation, agent rules, or skills must be uploaded to GitHub.

Use:

```powershell
git -c safe.directory=C:/punch_relay status --short
git -c safe.directory=C:/punch_relay diff
git -c safe.directory=C:/punch_relay add <changed-files>
git -c safe.directory=C:/punch_relay commit -m "<clear message>"
git -c safe.directory=C:/punch_relay push origin main
```

Before committing, confirm ignored sensitive files are not staged:

- `.env`
- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `bot.log`
- `.codex-remote-attachments/`

If push fails, do not force push, reset, rebase, or change remotes. Report the error and wait for user confirmation.
