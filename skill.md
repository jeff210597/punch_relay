---
name: punch-procedure
description: Repository-local operational skill for the punch_relay Discord punch bot. Use when diagnosing or changing punch eligibility, retry behavior, makeup buttons, duty/leave/cancel scheduling, e-HR comparison, admin alerts, or whether the bot should punch, retry, notify, or stop.
---

# Punch Procedure Skill

## Scope

Use this file for punch behavior decisions in this repository. Use `agent.md` for development workflow, validation, restart, and GitHub publishing.

## State Files

- `schedule_today.json`: today's random punch times. Do not re-randomize existing same-day schedules.
- `punched_today.json`: successful local punch keys. Write only after success.
- `admin_alerts_today.json`: admin alert de-duplication keys.
- `punch_data.json`: user binding, duty days, leave days, cancel dates, and notification settings.

## Punch Types And Keys

- 上班: `action="in"`, key `{uid}-in-{YYYY-MM-DD}`
- 下班: `action="out"`, key `{uid}-out-{YYYY-MM-DD}`
- 值班下班: `action="out"`, key `{uid}-dutyout-{YYYY-MM-DD}`

`dutyout` calls e-HR as an `out` punch but must use its own local key.

## Eligibility

Before automatic punching:

- Require `empid`, `password`, and `auto_punch=True`.
- Skip all automatic punch behavior for dates in `cancel_dates`, except no special override is currently allowed.
- Skip normal punch on leave days.
- Skip normal weekend punch unless the user is on duty today or was on duty yesterday.
- Duty day: punch `in`; do not punch normal `out`.
- Day after duty: skip `in`; punch `dutyout` when due.
- Leave or weekend after duty can still require `dutyout`.

## Due-Time Priority

When multiple keys are due, process in this order:

1. `dutyout`
2. normal `out`
3. `in`

This avoids an old missing morning key blocking afternoon or duty-after behavior.

## Success

On successful automatic punch:

- Notify the user.
- Save the correct key in `punched_today.json`.
- Do not send admin alert.
- Do not leave a retry queued for that key.

If e-HR confirmation is delayed, local success can still be saved. Final comparison may later detect that e-HR is missing and alert.

## Failure, Retry, And Makeup

On automatic punch failure:

- Notify the user.
- Send admin alert.
- Queue retry after 2 minutes if allowed.
- Retry at most 3 times.
- Send makeup button if allowed.

`in` rules:

- Retry and makeup only before 08:00.
- At or after 08:00, do not call `punch_clock(..., "in")`.
- If a queued `in` retry reaches 08:00, stop and alert.
- If an old `in` makeup button is pressed at or after 08:00, reject it and alert.

`out` and `dutyout` rules:

- Retry after 2 minutes, up to 3 times.
- Makeup button remains valid for 10 minutes.
- Final retry failure alerts admin and may send another makeup prompt.

## Makeup Button

Button timeout: 10 minutes.

On confirm:

1. Reload `punched_today.json`.
2. If the key already exists, do not punch again.
3. If action is `in` and current time is at or after 08:00, stop.
4. Otherwise call `punch_clock()`.
5. On success, save key and remove matching retry entry.
6. On failure, tell user to handle manually.

On cancel:

- Do not punch.
- Tell user to handle manually.

On timeout:

- Tell user the makeup request expired.

## e-HR Comparison

18:00 normal workday:

- Check normal `out`.
- If e-HR has `clock_out` or an inferred valid out time, stay silent.
- If missing, notify user, send makeup button, and alert admin.

09:00 duty-after day:

- Check `dutyout`.
- If e-HR has `clock_out` or inferred valid dutyout time, stay silent.
- If missing, notify user, send makeup button, and alert admin.

Do not treat expected skipped days as failures.

## Admin Alerts

Send admin alert for:

- Automatic punch failure.
- Retry stopped by 08:00 `in` cutoff.
- Retry failed 3 times.
- e-HR final comparison still missing.
- `in` makeup button pressed after 08:00.

Do not alert for:

- Successful punch.
- Expected skipped punch.
- e-HR comparison with confirmed record.

Destination:

- Prefer `ADMIN_ALERT_CHANNEL_ID=1514929880448630904` (`admin-alert`).
- Fall back to `ADMIN_IDS` only if the channel cannot be used.

Never include passwords, tokens, cookies, raw payloads, or full sensitive URLs.

## Manual Punch Exception

The explicit `/打卡` command is manual. The 08:00 cutoff applies to automatic retry and makeup button behavior, not to an explicit manual user command.
