import sys
import io
import os
import ctypes
import atexit

# 背景執行（工作排程器）時沒有終端機，將 stdout/stderr 導向 log 檔案
# 同時解決 cp950 無法處理 emoji 的問題
try:
    _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")
    _log_file = open(_log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file
except Exception:
    pass

# 強制切換到 bot 所在目錄，確保相對路徑讀取正確
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import discord
from discord import app_commands
import requests
import asyncio
import urllib3
import time
import json
import os
import re
import random
import calendar
from datetime import datetime, date, timedelta

urllib3.disable_warnings()

def load_local_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.lstrip("\ufeff").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.lstrip("\ufeff").strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ[key] = value
    except Exception as e:
        print(f"⚠️ .env 載入失敗：{e}")

load_local_env()

# Prevent two Windows sessions or two NSSM launches from running the bot together.
_instance_mutex = None

def acquire_single_instance():
    global _instance_mutex
    if os.name != "nt":
        return
    _instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\PunchRelayBotSingleInstance")
    if not _instance_mutex:
        print("❌ 已有另一個 Punch Relay Bot 實例正在執行，本次啟動結束。")
        raise SystemExit(2)
    if ctypes.windll.kernel32.GetLastError() == 183:
        # NSSM may report the old service stopped a moment before Python releases
        # the mutex. Wait briefly so a normal restart does not create a retry loop.
        wait_result = ctypes.windll.kernel32.WaitForSingleObject(_instance_mutex, 20000)
        if wait_result != 0:
            print("❌ 已有另一個 Punch Relay Bot 實例正在執行，本次啟動結束。")
            raise SystemExit(2)

# =====================
# 設定區（只需要改這裡）
# =====================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
NOTIFY_CHANNEL_ID = int(os.getenv("NOTIFY_CHANNEL_ID", "0"))
ADMIN_ALERT_CHANNEL_ID = int(os.getenv("ADMIN_ALERT_CHANNEL_ID", "0"))
DISCONNECT_RESTART_SECONDS = max(60, int(os.getenv("DISCONNECT_RESTART_SECONDS", "300")))

EHR_BASE = os.getenv("EHR_BASE", "").rstrip("/")
PUNCH_URL = f"{EHR_BASE}/servlet/jform"
FILE_PARAM = "hrm6p_edu.pkg,hrm6p.pkg,hrm6p_out_M1.pkg,hrm6fw_edu.pkg,hrm6bw.pkg,hrm6aw_edu.pkg,hrm6jw_edu.pkg,hrm6p_out_M21.pkg"

DATA_FILE = "punch_data.json"

RUNTIME_STATE_FILE = "bot_runtime_state.json"
RUNTIME_STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    RUNTIME_STATE_FILE
)

_previous_runtime_state = None
_previous_run_unclean = False
_runtime_started_at = None
_lifecycle_major_alert_pending = False


def _runtime_state_path(path=RUNTIME_STATE_PATH):
    return path


def load_runtime_state():
    """讀取上次 Bot 的執行狀態；壞掉或不存在時視為無紀錄。"""
    path = _runtime_state_path()

    try:
        if not os.path.exists(path):
            return None

        # utf-8-sig also accepts the BOM emitted by Windows PowerShell 5.
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

        return data if isinstance(data, dict) else None

    except Exception as e:
        print(f"⚠️ 讀取執行狀態檔失敗：{e}")
        return None


def save_runtime_state(state, reason=""):
    """原子寫入狀態，避免電腦突然斷電時寫出半截 JSON。"""
    path = _runtime_state_path()
    temp_path = f"{path}.tmp"
    payload = {
        "state": state,
        "reason": reason,
        "pid": os.getpid(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except Exception as e:
        print(f"⚠️ 寫入執行狀態檔失敗：{e}")


def mark_runtime_started():
    """只有真正取得單一執行個體後才呼叫。"""
    global _previous_runtime_state, _previous_run_unclean, _runtime_started_at
    global _lifecycle_major_alert_pending

    _previous_runtime_state = load_runtime_state()
    _previous_run_unclean = bool(
        _previous_runtime_state
        and _previous_runtime_state.get("state") == "running"
    )
    _lifecycle_major_alert_pending = False
    _runtime_started_at = datetime.now()
    save_runtime_state("running", "bot process started")

    if _previous_run_unclean:
        print(
            "⚠️ 偵測到上次執行未留下正常結束紀錄；"
            "可能是斷電、強制關機、崩潰或 NSSM 強制重啟。"
        )


def mark_runtime_clean_exit():
    """Python 正常離開時執行；斷電、os._exit、強制終止不會走到這裡。"""
    save_runtime_state("clean_exit", "python exited normally")

def validate_required_config():
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not EHR_BASE:
        missing.append("EHR_BASE")
    if NOTIFY_CHANNEL_ID <= 0:
        missing.append("NOTIFY_CHANNEL_ID")
    if missing:
        msg = (
            "❌ Bot 啟動設定不完整，已停止啟動。\n"
            f"缺少或無效設定：{', '.join(missing)}\n"
            "請確認 bot 所在資料夾的 .env 內已設定 DISCORD_TOKEN、NOTIFY_CHANNEL_ID、EHR_BASE。"
        )
        print(msg)
        raise SystemExit(1)

validate_required_config()

# =====================
# 隨機打卡時間設定
# 格式：(起始小時, 起始分鐘, 結束小時, 結束分鐘)
# =====================
PUNCH_IN_START   = (7,  0)   # 上班最早時間 07:00
PUNCH_IN_END     = (7, 40)   # 上班最晚時間 07:40
PUNCH_OUT_START  = (17,  5)  # 下班最早時間 17:05
PUNCH_OUT_END    = (17, 40)  # 下班最晚時間 17:40
DUTY_OUT_START   = (8,  5)   # 值班下班最早 08:05
DUTY_OUT_END     = (8, 40)   # 值班下班最晚 08:40

def get_random_punch_time(start_h, start_m, end_h, end_m):
    """在指定時間範圍內隨機產生打卡時間，回傳 (hour, minute) tuple"""
    start_total = start_h * 60 + start_m
    end_total = end_h * 60 + end_m
    total = random.randint(start_total, end_total)
    return (total // 60, total % 60)

# =====================
# 資料管理
# =====================
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_data(user_id):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "empid": None,
            "password": None,
            "auto_punch": True,
            "duty_days": [],
            "cancel_dates": [],
            "leave_dates": [],
            "notify": {
                "morning": True,
                "pre_punch": True,
                "compare": True,
                "monthly": True,
            }
        }
        save_data(data)
    # 補齊舊資料沒有 notify 欄位的情況
    if "notify" not in data[uid]:
        data[uid]["notify"] = {
            "morning": True,
            "pre_punch": True,
            "compare": True,
            "monthly": True,
        }
        save_data(data)
    if "monthly_auto_confirmations" not in data[uid]:
        data[uid]["monthly_auto_confirmations"] = {}
        save_data(data)
    return data[uid]

def save_user_data(user_id, user_data):
    data = load_data()
    data[str(user_id)] = user_data
    save_data(data)

def month_key(check_date):
    return check_date.strftime("%Y-%m")

def first_day_of_next_month(check_date=None):
    check_date = check_date or date.today()
    if check_date.month == 12:
        return date(check_date.year + 1, 1, 1)
    return date(check_date.year, check_date.month + 1, 1)

def month_key_for_next_month(check_date=None):
    return month_key(first_day_of_next_month(check_date))

def resolve_month_year(month_value, base_date=None):
    base_date = base_date or date.today()
    try:
        month = int(month_value)
    except Exception:
        raise ValueError("月份必須是 1~12")
    if month < 1 or month > 12:
        raise ValueError("月份必須是 1~12")
    year = base_date.year
    if month < base_date.month:
        year += 1
    return year, month

def parse_date_tokens(raw_dates, month_value=None, base_date=None):
    base_date = base_date or date.today()
    target_year = None
    target_month = None
    if month_value is not None:
        target_year, target_month = resolve_month_year(month_value, base_date)

    parsed, failed = [], []
    for raw in raw_dates.split():
        token = raw.strip().replace("／", "/")
        if not token:
            continue
        try:
            parts = token.split("/")
            if len(parts) == 1:
                if target_month is None:
                    failed.append(raw)
                    continue
                month = target_month
                day = int(parts[0])
                year = target_year
            elif len(parts) == 2:
                month = int(parts[0])
                day = int(parts[1])
                if target_month is not None and month != target_month:
                    failed.append(f"{raw}（月份與參數不符）")
                    continue
                year, month = resolve_month_year(month, base_date)
            else:
                failed.append(raw)
                continue
            dt = date(year, month, day)
            parsed.append((dt.strftime("%Y-%m-%d"), f"{dt.month}/{dt.day}", dt))
        except Exception:
            failed.append(raw)
    return parsed, failed

def mark_monthly_auto_confirmed(user_data, target_month_key, source):
    confirmations = user_data.setdefault("monthly_auto_confirmations", {})
    entry = confirmations.setdefault(target_month_key, {})
    entry["confirmed"] = True
    entry["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
    entry["source"] = source
    return entry

def is_monthly_auto_confirmed(user_data, target_month_key):
    return bool(
        user_data.get("monthly_auto_confirmations", {})
        .get(target_month_key, {})
        .get("confirmed")
    )

def mark_monthly_reminded(user_data, target_month_key, remind_date):
    confirmations = user_data.setdefault("monthly_auto_confirmations", {})
    entry = confirmations.setdefault(target_month_key, {})
    reminded_dates = entry.setdefault("reminded_dates", [])
    remind_str = remind_date.strftime("%Y-%m-%d")
    if remind_str in reminded_dates:
        return False
    reminded_dates.append(remind_str)
    return True

def should_disable_for_unconfirmed_month(user_data, check_date):
    if check_date.day != 1:
        return False
    if not user_data.get("empid") or not user_data.get("password"):
        return False
    return not is_monthly_auto_confirmed(user_data, month_key(check_date))

def monthly_disable_reason(check_date):
    return f"monthly_not_confirmed:{month_key(check_date)}"

def auto_disabled_by_monthly(user_data, check_date):
    return user_data.get("auto_punch_disabled_reason") == monthly_disable_reason(check_date)

def clear_monthly_disable_reason(user_data):
    if str(user_data.get("auto_punch_disabled_reason", "")).startswith("monthly_not_confirmed:"):
        user_data.pop("auto_punch_disabled_reason", None)

def format_month_schedule_summary(user_data, target_month_key):
    duty_days = []
    leave_days = []
    for value in sorted(user_data.get("duty_days", [])):
        if value.startswith(target_month_key):
            dt = date.fromisoformat(value)
            duty_days.append(f"{dt.month}/{dt.day}")
    for value in sorted(user_data.get("leave_dates", [])):
        if value.startswith(target_month_key):
            dt = date.fromisoformat(value)
            leave_days.append(f"{dt.month}/{dt.day}")
    duty_text = ", ".join(duty_days) if duty_days else "尚未設定"
    leave_text = ", ".join(leave_days) if leave_days else "尚未設定"
    return f"值班：{duty_text}\n休假：{leave_text}"

def format_schedule_dates_for_month(user_data, target_month_key, weekday_names=None):
    weekday_names = weekday_names or ["一", "二", "三", "四", "五", "六", "日"]

    def collect(field):
        items = []
        for value in sorted(user_data.get(field, [])):
            if value.startswith(f"{target_month_key}-"):
                try:
                    dt = date.fromisoformat(value)
                    wd = weekday_names[dt.weekday()]
                    items.append(f"{dt.month}/{dt.day}（{wd}）")
                except Exception:
                    items.append(value)
        return "、".join(items) if items else "無"

    return collect("duty_days"), collect("leave_dates")

def future_schedule_month_keys(user_data, start_month_key):
    months = set()
    for field in ("duty_days", "leave_dates"):
        for value in user_data.get(field, []):
            if len(value) >= 7:
                mk = value[:7]
                if mk >= start_month_key:
                    months.add(mk)
    return sorted(months)

def format_month_schedule_section(user_data, target_month_key, title, include_confirmation=False):
    lines = [f"**{title}（{target_month_key}）**"]
    if include_confirmation:
        done = "✅ 已完成" if is_monthly_auto_confirmed(user_data, target_month_key) else "❌ 未完成"
        lines.append(f"下月設定：{done}")
    duty_text, leave_text = format_schedule_dates_for_month(user_data, target_month_key)
    lines.append(f"值班：{duty_text}")
    lines.append(f"休假：{leave_text}")
    return "\n".join(lines)

def format_future_month_schedule_sections(user_data, base_date=None, include_current=True, include_next=True, max_extra_months=None):
    base_date = base_date or date.today()
    current_key = month_key(base_date)
    next_key = month_key_for_next_month(base_date)
    month_keys = future_schedule_month_keys(user_data, current_key)
    sections = []

    if include_current:
        sections.append(format_month_schedule_section(user_data, current_key, "本月"))
    if include_next:
        sections.append(format_month_schedule_section(user_data, next_key, "下月", include_confirmation=True))

    extra_keys = [mk for mk in month_keys if mk not in {current_key, next_key}]
    shown_extra_keys = extra_keys if max_extra_months is None else extra_keys[:max_extra_months]
    if shown_extra_keys:
        for mk in shown_extra_keys:
            sections.append(format_month_schedule_section(user_data, mk, "後續月份"))
    elif max_extra_months is None:
        sections.append("**後續月份**\n目前無設定")

    remaining = max(0, len(extra_keys) - len(shown_extra_keys))
    if remaining:
        sections.append(f"...另有 {remaining} 個後續月份已設定")
    return sections

def remove_month_dates(user_data, field, target_month_key):
    user_data[field] = [
        value for value in user_data.get(field, [])
        if not value.startswith(f"{target_month_key}-")
    ]

def apply_next_month_settings(user_data, duty_dates=None, leave_dates=None, no_special_days=False, source="next_month_settings"):
    target_month_date = first_day_of_next_month()
    target_month_key = month_key(target_month_date)
    duty_dates = (duty_dates or "").strip()
    leave_dates = (leave_dates or "").strip()

    if no_special_days and (duty_dates or leave_dates):
        return {
            "success": False,
            "message": "請擇一：選擇「無值班無休假」時，不要同時填值班或休假日期。",
            "target_month": target_month_key,
        }
    if not no_special_days and not duty_dates and not leave_dates:
        return {
            "success": False,
            "message": "請選擇「無值班無休假」，或填入下月值班/休假日期。",
            "target_month": target_month_key,
        }

    parsed_duty, failed_duty = ([], [])
    parsed_leave, failed_leave = ([], [])
    if duty_dates:
        parsed_duty, failed_duty = parse_date_tokens(duty_dates, target_month_date.month)
    if leave_dates:
        parsed_leave, failed_leave = parse_date_tokens(leave_dates, target_month_date.month)

    failed = []
    failed.extend([f"值班 {item}" for item in failed_duty])
    failed.extend([f"休假 {item}" for item in failed_leave])
    if failed:
        return {
            "success": False,
            "message": f"日期格式有誤：{', '.join(failed)}",
            "target_month": target_month_key,
        }

    user_data.setdefault("duty_days", [])
    user_data.setdefault("leave_dates", [])
    if no_special_days:
        remove_month_dates(user_data, "duty_days", target_month_key)
        remove_month_dates(user_data, "leave_dates", target_month_key)
        source = f"{source}_no_special_days"
    else:
        if duty_dates:
            remove_month_dates(user_data, "duty_days", target_month_key)
            for date_str, _display, _dt in parsed_duty:
                if date_str not in user_data["duty_days"]:
                    user_data["duty_days"].append(date_str)
        if leave_dates:
            remove_month_dates(user_data, "leave_dates", target_month_key)
            for date_str, _display, _dt in parsed_leave:
                if date_str not in user_data["leave_dates"]:
                    user_data["leave_dates"].append(date_str)

    user_data["duty_days"] = sorted(user_data.get("duty_days", []))
    user_data["leave_dates"] = sorted(user_data.get("leave_dates", []))
    mark_monthly_auto_confirmed(user_data, target_month_key, source)

    duty_added = [display for _date_str, display, _dt in parsed_duty]
    leave_added = [display for _date_str, display, _dt in parsed_leave]
    return {
        "success": True,
        "target_month": target_month_key,
        "duty_added": duty_added,
        "leave_added": leave_added,
        "summary": format_month_schedule_summary(user_data, target_month_key),
    }

def build_monthly_binding_admin_summary(data, target_month_key):
    completed = []
    missing = []
    for uid, ud in sorted(data.items()):
        if not ud.get("empid") or not ud.get("password"):
            continue
        line = f"<@{uid}>（{ud.get('empid')}）"
        if is_monthly_auto_confirmed(ud, target_month_key):
            source = (
                ud.get("monthly_auto_confirmations", {})
                .get(target_month_key, {})
                .get("source", "unknown")
            )
            completed.append(f"✅ {line}：已完成（{source}）")
        else:
            missing.append(f"❌ {line}：未完成")
    lines = [f"📋 **{target_month_key} 下月設定完成狀態**"]
    lines.append("")
    lines.append("**已完成**")
    lines.extend(completed or ["（無）"])
    lines.append("")
    lines.append("**未完成**")
    lines.extend(missing or ["（無）"])
    return "\n".join(lines)

def load_monthly_binding_admin_summary_state():
    try:
        if os.path.exists(MONTHLY_BINDING_ADMIN_SUMMARY_FILE):
            with open(MONTHLY_BINDING_ADMIN_SUMMARY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def save_monthly_binding_admin_summary_state(state):
    try:
        with open(MONTHLY_BINDING_ADMIN_SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"月底綁定管理摘要狀態儲存失敗：{e}")

async def send_admin_channel_message(client, message):
    if ADMIN_ALERT_CHANNEL_ID <= 0:
        print("ADMIN_ALERT_CHANNEL_ID 未設定，無法發送管理通知。")
        return False
    try:
        channel = client.get_channel(ADMIN_ALERT_CHANNEL_ID) or await client.fetch_channel(ADMIN_ALERT_CHANNEL_ID)
        await channel.send(message)
        return True
    except Exception as e:
        print(f"管理通知發送失敗：{e}")
        return False

def is_in_last_days_of_month(check_date, day_count):
    last_day = calendar.monthrange(check_date.year, check_date.month)[1]
    return check_date.day >= last_day - day_count + 1

def enable_auto_punch(user_data, check_date=None):
    """Enable auto punch and clear the one-day cancellation for check_date."""
    check_date = check_date or date.today()
    today_str = check_date.strftime("%Y-%m-%d")
    cancel_dates = user_data.setdefault("cancel_dates", [])
    was_cancelled = today_str in cancel_dates
    if was_cancelled:
        cancel_dates.remove(today_str)
    user_data["auto_punch"] = True
    clear_monthly_disable_reason(user_data)
    return was_cancelled

def mark_rebind_confirm_only(user_data):
    """After binding, past-due punches require user confirmation instead of auto catch-up."""
    now = datetime.now()
    today_str = date.today().strftime("%Y-%m-%d")
    user_data["rebind_confirm_only"] = {
        "date": today_str,
        "bound_at_min": now.hour * 60 + now.minute,
        "notified_keys": [],
    }

def should_confirm_after_rebind(user_data, today_str, punch_key, scheduled_min):
    rule = user_data.get("rebind_confirm_only") or {}
    if rule.get("date") != today_str:
        return False
    try:
        bound_at_min = int(rule.get("bound_at_min", -1))
    except Exception:
        return False
    notified = set(rule.get("notified_keys", []))
    return 0 <= scheduled_min <= bound_at_min and punch_key not in notified

def mark_rebind_confirm_notified(user_id, user_data, punch_key):
    rule = user_data.setdefault("rebind_confirm_only", {})
    notified = rule.setdefault("notified_keys", [])
    if punch_key not in notified:
        notified.append(punch_key)
    save_user_data(user_id, user_data)

def validate_ehr_login(empid, password):
    """只驗證 e-HR 帳密，不執行打卡。"""
    if not EHR_BASE:
        return {"success": False, "message": "e-HR 網址尚未設定，請確認 EHR_BASE 環境變數"}
    session = requests.Session()
    try:
        session.get(f"{PUNCH_URL}?file={FILE_PARAM}", timeout=10, verify=False)
        login_resp = session.post(
            PUNCH_URL,
            data={
                "file": FILE_PARAM,
                "uid": empid,
                "pwd": password,
                "image.x": "0",
                "image.y": "0",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
            verify=False
        )
        login_resp.raise_for_status()
        if "hrlogin" in login_resp.text.lower():
            return {"success": False, "message": "登入失敗，請確認員工編號或密碼"}

        # 登入 POST 未回到 hrlogin 並不代表帳密正確；錯誤頁或非預期回應也可能
        # 沒有該字串。必須再開啟登入後的打卡頁，並取得該頁才會產生的 enc，
        # 才能明確判定 session 已通過驗證。
        punch_page = session.get(
            f"{PUNCH_URL}?file={FILE_PARAM}&init_func=B8.%E7%B7%9A%E4%B8%8A%E7%B0%BD%E5%88%B0%E7%B0%BD%E9%80%80.",
            timeout=10,
            verify=False,
        )
        punch_page.raise_for_status()
        if "hrlogin" in punch_page.text.lower():
            return {"success": False, "message": "登入失敗，請確認員工編號或密碼"}
        enc_match = re.search(
            r'name\s*=\s*["\']?enc["\']?\s+value\s*=\s*["\']([^"\']+)["\']',
            punch_page.text,
            re.IGNORECASE,
        )
        if not enc_match:
            return {
                "success": False,
                "message": "無法確認 e-HR 登入成功，帳號未綁定；請稍後重試或聯絡管理員",
            }
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"登入驗證連線失敗：{str(e)}"}

# =====================
# 加密邏輯
# =====================
def lzw_encode(s):
    if len(s) == 0:
        return s
    data = list(s)
    dict_ = {}
    out = []
    phrase = data[0]
    code = 256
    for i in range(1, len(data)):
        currChar = data[i]
        if dict_.get(phrase + currChar) is not None:
            phrase += currChar
        else:
            if len(phrase) > 1:
                out.append(dict_.get(phrase, ord(phrase[0])))
            else:
                out.append(ord(phrase[0]))
            dict_[phrase + currChar] = code
            code += 1
            phrase = currChar
    if len(phrase) > 1:
        out.append(dict_.get(phrase, ord(phrase[0])))
    else:
        out.append(ord(phrase[0]))
    result = ""
    for i in range(len(out)):
        result += chr(out[i])
    return result

def make_enc(empid, password):
    raw = f"{empid}|{password}"
    encoded = lzw_encode(raw)
    result = ""
    for c in encoded:
        result += format(ord(c), '02x')
    return result

def _t2m(t_str):
    """HH:MM 字串轉分鐘數，解析失敗回傳 -1"""
    try:
        return int(t_str[:2]) * 60 + int(t_str[3:])
    except:
        return -1

def infer_punch_times(result, is_duty_after):
    """
    依四線優先順序推算顯示用的上班/下班時間與來源標籤。
    回傳 dict：
      inferred_in      : 推算上班時間（str 或 None）
      inferred_out     : 推算下班/值班下班時間（str 或 None）
      in_source        : "ehr"（①）| "times"（②）| None
      out_source       : "ehr"（①）| "times"（②）| None
    注意：punched_today（③）由呼叫端自行處理，因為需要知道 uid/key。
    """
    clock_in  = result.get("clock_in")   # e-HR index5
    clock_out = result.get("clock_out")  # e-HR index6
    clock_out_source = result.get("clock_out_source", "ehr") if clock_out else None
    times     = result.get("times", [])  # index10 所有原始記錄（已排序）

    BDRY_DUTY = 8 * 60   # 08:00
    BDRY_OUT  = 17 * 60  # 17:00

    # ── 上班 ──
    inferred_in = None
    in_source   = None
    if not is_duty_after:
        # 值班當天或平日：取上班時間
        if clock_in:
            inferred_in = clock_in
            in_source   = "ehr"
        elif times:
            # 取最早筆（不限時間，人資規則：最早刷卡算上班）
            inferred_in = times[0]
            in_source   = "times"

    # ── 下班 / 值班下班 ──
    inferred_out = None
    out_source   = None
    if clock_out:
        inferred_out = clock_out
        out_source   = clock_out_source
    elif times:
        if is_duty_after:
            # 值班隔天：取 ≥08:00 的最晚筆，< 08:00 的忽略
            candidates = [t for t in times if _t2m(t) >= BDRY_DUTY]
            if candidates:
                inferred_out = candidates[-1]
                out_source   = "times"
        else:
            # 平日：取 ≥17:00 的最晚筆
            candidates = [t for t in times if _t2m(t) >= BDRY_OUT]
            if candidates:
                inferred_out = candidates[-1]
                out_source   = "times"

    return {
        "inferred_in":  inferred_in,
        "inferred_out": inferred_out,
        "in_source":    in_source,
        "out_source":   out_source,
    }

def fmt_source(source, is_in=True):
    """根據來源回傳括號說明文字"""
    kind = "刷卡" if is_in else "刷退"
    if source == "ehr":
        return f"e-HR {kind}時間"
    elif source == "ehr_next_day":
        return f"e-HR {kind}時間（隔天）"
    elif source == "times":
        return "e-HR 有記錄但刷卡/出卡時間待定"
    return ""

def punch_clock(empid, password, action):
    session = requests.Session()
    try:
        session.get(f"{PUNCH_URL}?file={FILE_PARAM}", timeout=10, verify=False)
    except:
        pass

    login_payload = {
        "file": FILE_PARAM,
        "uid": empid,
        "pwd": password,
        "image.x": "0",
        "image.y": "0",
    }
    try:
        login_resp = session.post(
            PUNCH_URL,
            data=login_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15, verify=False
        )
        if "hrlogin" in login_resp.text:
            return {"success": False, "message": "❌ 登入失敗，請確認帳號密碼"}
    except Exception as e:
        return {"success": False, "message": f"❌ 登入連線錯誤：{str(e)}"}

    try:
        punch_page = session.get(
            f"{PUNCH_URL}?file={FILE_PARAM}&init_func=B8.%E7%B7%9A%E4%B8%8A%E7%B0%BD%E5%88%B0%E7%B0%BD%E9%80%80.",
            timeout=10, verify=False
        )
        enc_match = re.search(r'name=enc\s+value="([^"]+)"', punch_page.text)
        enc = enc_match.group(1) if enc_match else make_enc(empid, password)
    except:
        enc = make_enc(empid, password)

    timestamp = str(int(time.time() * 1000))
    buttonid = "button1" if action == "in" else "button2"

    punch_payload = {
        "time": timestamp,
        "em_step": "ajax",
        "encodeURIComponent": "1",
        "FUNCTION_NAME": "B8.線上簽到簽退.",
        "act": "", "init_func": "", "flow_approve": "",
        "buttonid": buttonid,
        "buttonlink": "Ajax(No refresh Browser)",
        "fromlink": "", "table_data": "", "form_target": "",
        "menu_display_flag": "", "em_POSITION": "1",
        "file": FILE_PARAM,
        "enc": enc,
        "REMEMBER": empid,
        "user_id": empid,
        "CONDITION_B": f"+and+a.EMPID%3D%27{empid}%27",
        "ITEM": "A",
        "EMPID": empid,
        "PASS": password,
        "timer": "",
    }

    try:
        resp = session.post(
            PUNCH_URL,
            data=punch_payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{PUNCH_URL}?file={FILE_PARAM}",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15, verify=False
        )
        text = resp.text
        action_text = "上班" if action == "in" else "下班"
        if "<Message>" in text and "<data>" in text:
            # 打卡成功後等3秒再查詢確認記錄
            time.sleep(3)
            query_result = _query_today_from_monthly_b9(empid, password)
            if query_result.get("success") and query_result.get("times"):
                times_str = "、".join(query_result["times"])
                return {
                    "success": True,
                    "message": f"✅ {action_text}打卡成功！",
                    "confirmed": True,
                    "times": times_str
                }
            else:
                return {
                    "success": True,
                    "message": f"✅ {action_text}打卡成功！",
                    "confirmed": False
                }
        elif "hrlogin" in text:
            return {"success": False, "message": "❌ Session 過期，請重試"}
        else:
            return {"success": False, "message": "❌ 打卡失敗，請確認帳號密碼"}
    except Exception as e:
        return {"success": False, "message": f"❌ 連線錯誤：{str(e)}"}

# =====================
# 打卡失敗重試佇列
# =====================
retry_queue = []  # list of dict: {uid, empid, password, action, label, retry_at, attempts}

# =====================
# 排程邏輯
# =====================
def is_duty_day(user_data, check_date):
    return check_date.strftime("%Y-%m-%d") in user_data.get("duty_days", [])

def is_auto_cancelled(user_data, check_date):
    return check_date.strftime("%Y-%m-%d") in user_data.get("cancel_dates", [])

def is_leave_day(user_data, check_date):
    return check_date.strftime("%Y-%m-%d") in user_data.get("leave_dates", [])

def is_weekend(check_date):
    return check_date.weekday() >= 5

def is_duty_after_day(user_data, check_date):
    return is_duty_day(user_data, check_date - timedelta(days=1))

def expects_regular_out_check(user_data, check_date):
    return (
        user_data.get("auto_punch", True)
        and not is_auto_cancelled(user_data, check_date)
        and not is_leave_day(user_data, check_date)
        and not is_weekend(check_date)
        and not is_duty_day(user_data, check_date)
        and not is_duty_after_day(user_data, check_date)
    )

def expects_dutyout_check(user_data, check_date):
    return (
        (
            user_data.get("auto_punch", True)
            or auto_disabled_by_monthly(user_data, check_date)
        )
        and not is_auto_cancelled(user_data, check_date)
        and is_duty_after_day(user_data, check_date)
    )

def get_today_schedule(user_data):
    today = date.today()
    yesterday = today - timedelta(days=1)
    is_duty_after = is_duty_day(user_data, yesterday)

    if not user_data.get("empid"):
        return "尚未綁定帳號\n請使用 `/帳號綁定` 開始設定"
    if is_auto_cancelled(user_data, today):
        return "⏸️ 今日已取消自動打卡（手動模式）\n請使用 `/打卡` 手動打卡"

    # 昨日值班時，今天的主要打卡模式是「值班隔天」。即使今天是平日、
    # 週末或休假，仍須完成昨日值班的下班卡，不能顯示一般日排程。
    if is_duty_after:
        h_duty, m_duty = DUTY_OUT_START[0], DUTY_OUT_START[1]
        h_duty_e, m_duty_e = DUTY_OUT_END[0], DUTY_OUT_END[1]
        return "\n".join([
            "🌙 今日為值班隔天",
            "不打上班卡",
            f"⏰ {h_duty:02d}:{m_duty:02d}~{h_duty_e:02d}:{m_duty_e:02d} 自動打值班下班卡（昨日值班）",
        ])

    if not user_data.get("auto_punch", True):
        if auto_disabled_by_monthly(user_data, today):
            return "⚠️ 本月尚未完成續用確認，自動打卡已關閉\n請使用 `/自動打卡恢復` 或聯絡管理員"
        return "⚠️ 自動打卡已關閉\n請使用 `/自動打卡恢復` 開啟"

    if is_leave_day(user_data, today):
        return "🏖️ 今日為休假日\n不會自動打卡"

    h_in, m_in = PUNCH_IN_START[0], PUNCH_IN_START[1]
    h_in_e, m_in_e = PUNCH_IN_END[0], PUNCH_IN_END[1]
    h_out, m_out = PUNCH_OUT_START[0], PUNCH_OUT_START[1]
    h_out_e, m_out_e = PUNCH_OUT_END[0], PUNCH_OUT_END[1]
    h_duty, m_duty = DUTY_OUT_START[0], DUTY_OUT_START[1]
    h_duty_e, m_duty_e = DUTY_OUT_END[0], DUTY_OUT_END[1]

    if is_weekend(today):
        if is_duty_day(user_data, today):
            lines = [
                "🌙 今日為週末值班日",
                f"⏰ {h_in:02d}:{m_in:02d}~{h_in_e:02d}:{m_in_e:02d} 自動打上班卡",
                f"（值班下班卡將於明天 {h_duty:02d}:{m_duty:02d}~{h_duty_e:02d}:{m_duty_e:02d} 自動打）"
            ]
        else:
            lines = [
                "📴 今日為週末（六日）",
                "自動打卡不啟動",
                "若有值班請使用 `/值班休假設定` 或手動 `/打卡`"
            ]
        return "\n".join(lines)

    lines = []
    if is_duty_day(user_data, today):
        lines.append("🌙 今日為值班日")
        lines.append(f"⏰ {h_in:02d}:{m_in:02d}~{h_in_e:02d}:{m_in_e:02d} 自動打上班卡")
        lines.append(f"（值班下班卡將於明天 {h_duty:02d}:{m_duty:02d}~{h_duty_e:02d}:{m_duty_e:02d} 自動打）")
    else:
        lines.append("🟢 今日為平日")
        lines.append(f"⏰ {h_in:02d}:{m_in:02d}~{h_in_e:02d}:{m_in_e:02d} 自動打上班卡")
        lines.append(f"⏰ {h_out:02d}:{m_out:02d}~{h_out_e:02d}:{m_out_e:02d} 自動打下班卡")

    return "\n".join(lines)

# 全域隨機時間表（讓查詢指令可以讀取）
scheduled_times = {}

PUNCHED_FILE = "punched_today.json"
SCHEDULE_FILE = "schedule_today.json"
ADMIN_ALERTS_FILE = "admin_alerts_today.json"
MONTHLY_BINDING_ADMIN_SUMMARY_FILE = "monthly_binding_admin_summary.json"
MONTHLY_SUMMARY_SENT_FILE = "monthly_summary_sent.json"
RETRY_DELAY_MINUTES = 2
MAX_RETRY_ATTEMPTS = 3
PUNCH_IN_CUTOFF_MIN = 8 * 60

def load_punched_today():
    """從檔案載入今天已打卡記錄，回傳 dict {key: "HH:MM"}"""
    try:
        if os.path.exists(PUNCHED_FILE):
            with open(PUNCHED_FILE, "r") as f:
                data = json.load(f)
            today_str = date.today().strftime("%Y-%m-%d")
            if data.get("date") == today_str:
                punched = data.get("punched", {})
                # 相容舊格式（list）：轉成 dict，時間設為空字串
                if isinstance(punched, list):
                    return {k: "" for k in punched}
                return dict(punched)
    except:
        pass
    return {}

def save_punched_today(punched_dict, today_str):
    """把今天已打卡記錄存到檔案，punched_dict 格式：{key: "HH:MM"}"""
    try:
        with open(PUNCHED_FILE, "w") as f:
            json.dump({"date": today_str, "punched": punched_dict}, f)
    except:
        pass

def load_schedule_today():
    """從檔案載入今天的隨機打卡時間（重啟後保持一致）"""
    try:
        if os.path.exists(SCHEDULE_FILE):
            with open(SCHEDULE_FILE, "r") as f:
                data = json.load(f)
            today_str = date.today().strftime("%Y-%m-%d")
            if data.get("date") == today_str:
                return data.get("schedules", {})
    except:
        pass
    return {}

def save_schedule_today(schedules, today_str):
    """把今天的隨機打卡時間存到檔案"""
    try:
        with open(SCHEDULE_FILE, "w") as f:
            json.dump({"date": today_str, "schedules": schedules}, f)
    except:
        pass

def load_admin_alerts_today():
    """載入今日已發送的管理員告警 key，避免重複洗版。"""
    try:
        if os.path.exists(ADMIN_ALERTS_FILE):
            with open(ADMIN_ALERTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            today_str = date.today().strftime("%Y-%m-%d")
            if data.get("date") == today_str:
                alerts = data.get("alerts", [])
                return set(alerts) if isinstance(alerts, list) else set()
    except:
        pass
    return set()

def save_admin_alerts_today(alerts, today_str):
    """儲存今日已發送的管理員告警 key。"""
    try:
        with open(ADMIN_ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": today_str, "alerts": sorted(alerts)}, f, ensure_ascii=False, indent=2)
    except:
        pass

def load_monthly_summary_sent_state():
    try:
        if os.path.exists(MONTHLY_SUMMARY_SENT_FILE):
            with open(MONTHLY_SUMMARY_SENT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def save_monthly_summary_sent_state(state):
    try:
        with open(MONTHLY_SUMMARY_SENT_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"月底摘要狀態儲存失敗：{e}")

def _admin_alert_key(uid, label, today_str, alert_type):
    safe_label = (label or "unknown").replace(" ", "_")
    return f"{uid}-{safe_label}-{today_str}-{alert_type}"

async def send_admin_alert(client, uid, empid, label, alert_type, reason, status, scheduled_time=None):
    """發送管理員異常告警；優先送私人頻道，失敗則私訊 ADMIN_IDS。"""
    today_str = date.today().strftime("%Y-%m-%d")
    alerts = load_admin_alerts_today()
    alert_key = _admin_alert_key(uid, label, today_str, alert_type)
    if alert_key in alerts:
        return

    lines = [
        "🚨 **打卡異常告警**",
        f"日期：{today_str}",
        f"使用者：<@{uid}>",
        f"員工編號：{empid}",
        f"卡別：{label}",
    ]
    if scheduled_time:
        lines.append(f"排程時間：{scheduled_time}")
    lines.extend([
        f"異常原因：{reason}",
        f"處理狀態：{status}",
    ])
    message = "\n".join(lines)

    sent = False
    if ADMIN_ALERT_CHANNEL_ID > 0:
        try:
            channel = client.get_channel(ADMIN_ALERT_CHANNEL_ID) or await client.fetch_channel(ADMIN_ALERT_CHANNEL_ID)
            if channel:
                await channel.send(message)
                sent = True
        except Exception as e:
            print(f"管理員告警頻道發送失敗：{e}")

    if not sent:
        for admin_id in globals().get("ADMIN_IDS", set()):
            try:
                admin_user = client.get_user(int(admin_id)) or await client.fetch_user(int(admin_id))
                await admin_user.send(message)
                sent = True
            except Exception as e:
                print(f"管理員告警私訊失敗 {admin_id}：{e}")

    if sent:
        alerts.add(alert_key)
        save_admin_alerts_today(alerts, today_str)

async def auto_punch_task(client):
    await client.wait_until_ready()
    print("✅ 自動打卡排程啟動")

    # 每天重新產生隨機打卡時間
    global scheduled_times
    # 從檔案載入今天已打卡記錄（重啟後不會重複打卡），格式：{key: "HH:MM"}
    punched_today = load_punched_today()
    # 從檔案載入今天的隨機時間（重啟後保持同一組時間）
    saved_schedules = load_schedule_today()
    if saved_schedules:
        scheduled_times.update(saved_schedules)
        print(f"📋 載入今日打卡時間：{len(saved_schedules)} 人的排程")
    last_date = date.today().strftime("%Y-%m-%d")
    print(f"📋 載入今日已打卡記錄：{len(punched_today)} 筆")

    # 等 Bot 完全就緒。逾時未打的下班/值班下班卡交給主迴圈補跑；
    # 若在這裡先發補打按鈕，主迴圈仍會自動打卡，會造成「按鈕未按就成功」的重複通知。
    await asyncio.sleep(5)  # 等 Bot 完全就緒

    while not client.is_closed():
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_min = now.hour * 60 + now.minute
        today = date.today()
        yesterday = today - timedelta(days=1)
        today_str = today.strftime("%Y-%m-%d")

        # 每天 00:00 重設
        if last_date != today_str:
            punched_today.clear()  # dict.clear() works fine
            save_punched_today(punched_today, today_str)
            scheduled_times = {}
            save_schedule_today({}, today_str)
            last_date = today_str
            print(f"📅 新的一天：{today_str}，重設打卡排程")

        data = load_data()

        if today.day == 1:
            changed_monthly = False
            for uid_month, ud_month in data.items():
                if not ud_month.get("empid") or not ud_month.get("password"):
                    continue
                reason = monthly_disable_reason(today)
                if is_monthly_auto_confirmed(ud_month, month_key(today)):
                    if (
                        not ud_month.get("auto_punch", True)
                        and str(ud_month.get("auto_punch_disabled_reason", "")).startswith("monthly_not_confirmed:")
                    ):
                        ud_month["auto_punch"] = True
                        clear_monthly_disable_reason(ud_month)
                        changed_monthly = True
                        print(f"📅 {uid_month} 已完成 {month_key(today)} 續用確認，月初恢復自動打卡")
                elif should_disable_for_unconfirmed_month(ud_month, today):
                    if ud_month.get("auto_punch") or ud_month.get("auto_punch_disabled_reason") != reason:
                        ud_month["auto_punch"] = False
                        ud_month["auto_punch_disabled_reason"] = reason
                        changed_monthly = True
                        print(f"📅 {uid_month} 未完成 {month_key(today)} 下月設定，已關閉自動打卡")
            if changed_monthly:
                save_data(data)

        if current_time == "08:00" and is_in_last_days_of_month(today, 5):
            target_month = month_key_for_next_month(today)
            reminder_changed = False
            for uid_remind, ud_remind in data.items():
                if not ud_remind.get("empid") or not ud_remind.get("password"):
                    continue
                if is_monthly_auto_confirmed(ud_remind, target_month):
                    continue
                if not mark_monthly_reminded(ud_remind, target_month, today):
                    continue
                reminder_changed = True
                reminder_msg = (
                    f"📌 **下月設定提醒**\n"
                    f"請在月底前完成 **{target_month}** 下月設定，確認是否繼續使用自動打卡。\n\n"
                    "完成方式：\n"
                    "1. 按「是，下月無值班無休假」\n"
                    "2. 按「否，我要填值班/休假」輸入下月日期\n"
                    "3. 或使用 `/下月設定` 叫出同樣的選項\n\n"
                    f"{format_month_schedule_summary(ud_remind, target_month)}\n\n"
                    "若月底前未完成，下個月 1 號會自動關閉自動打卡。"
                )
                try:
                    remind_user = client.get_user(int(uid_remind)) or await client.fetch_user(int(uid_remind))
                    await remind_user.send(
                        reminder_msg,
                        view=PersistentNextMonthReminderView(),
                    )
                except Exception as e:
                    print(f"下月設定提醒私訊失敗 {uid_remind}：{e}")
                await send_admin_channel_message(
                    client,
                    f"📌 下月設定提醒已發送：<@{uid_remind}>（{ud_remind.get('empid')}）→ {target_month}"
                )
            if reminder_changed:
                save_data(data)

        if current_time == "09:00" and (today + timedelta(days=1)).month != today.month:
            target_month = month_key_for_next_month(today)
            summary_state = load_monthly_binding_admin_summary_state()
            sent_key = f"{today.strftime('%Y-%m-%d')}:{target_month}"
            if sent_key not in summary_state.get("sent", []):
                sent = summary_state.setdefault("sent", [])
                await send_admin_channel_message(
                    client,
                    build_monthly_binding_admin_summary(data, target_month)
                )
                sent.append(sent_key)
                save_monthly_binding_admin_summary_state(summary_state)

        for uid, user_data in data.items():
            empid = user_data.get("empid")
            password = user_data.get("password")

            if not empid or not password:
                continue
            if not user_data.get("auto_punch", True):
                if not (auto_disabled_by_monthly(user_data, today) and is_duty_day(user_data, yesterday)):
                    continue
                if is_auto_cancelled(user_data, today):
                    continue
                print(f"🌙 {uid} 本月未確認續用，但保留跨月值班下班卡流程")
            if not user_data.get("auto_punch", True) and not is_duty_day(user_data, yesterday):
                continue

            # 為每個使用者產生今天的隨機時間（只產生一次）
            if uid not in scheduled_times:
                h_in, m_in = get_random_punch_time(*PUNCH_IN_START, *PUNCH_IN_END)
                h_out, m_out = get_random_punch_time(*PUNCH_OUT_START, *PUNCH_OUT_END)
                h_duty, m_duty = get_random_punch_time(*DUTY_OUT_START, *DUTY_OUT_END)
                scheduled_times[uid] = {
                    "in":      f"{h_in:02d}:{m_in:02d}",
                    "out":     f"{h_out:02d}:{m_out:02d}",
                    "dutyout": f"{h_duty:02d}:{m_duty:02d}",
                }
                # 存到檔案，重啟後保持一致
                save_schedule_today(scheduled_times, today_str)
                print(f"⏰ {uid} 今日排程：上班 {scheduled_times[uid]['in']}，下班 {scheduled_times[uid]['out']}，值班下班 {scheduled_times[uid]['dutyout']}")

            times = scheduled_times[uid]
            action = None
            label = ""
            in_due = 0 <= _t2m(times["in"]) <= current_min
            out_due = 0 <= _t2m(times["out"]) <= current_min
            dutyout_due = 0 <= _t2m(times["dutyout"]) <= current_min

            # 已到期的下班卡要優先於上班卡判斷。
            # 若早上本地記錄缺漏，使用 elif 會讓「上班已過期」一直擋住下午下班卡。
            # 值班下班卡（昨天是值班日就打，請假日也要打）
            if dutyout_due:
                if is_duty_day(user_data, yesterday):
                    if not is_auto_cancelled(user_data, today):  # 取消自動打卡才跳過
                        key = f"{uid}-dutyout-{today_str}"
                        if key not in punched_today:
                            action = "out"
                            label = "值班下班"

            # 下班卡（請假日或取消自動打卡則跳過，平日非值班，且今天沒打過值班下班卡）
            if action is None and out_due:
                if is_auto_cancelled(user_data, today) or is_leave_day(user_data, today):
                    pass  # 請假或取消自動打卡不打下班卡
                elif not is_weekend(today) and not is_duty_day(user_data, today) and not is_duty_day(user_data, yesterday):
                    dutyout_key = f"{uid}-dutyout-{today_str}"
                    if dutyout_key not in punched_today:
                        key = f"{uid}-out-{today_str}"
                        if key not in punched_today:
                            action = "out"
                            label = "下班"

            # 上班卡（請假日或取消自動打卡則跳過，值班隔天也跳過）
            if action is None and in_due and current_min < 8 * 60:
                if is_auto_cancelled(user_data, today) or is_leave_day(user_data, today):
                    pass  # 休假或取消自動打卡不打上班卡
                elif is_weekend(today) and not is_duty_day(user_data, today):
                    pass  # 週末非值班不打
                elif is_duty_day(user_data, yesterday):
                    pass  # 值班隔天不打上班卡，只打值班下班卡
                else:
                    key = f"{uid}-in-{today_str}"
                    if key not in punched_today:
                        action = "in"
                        label = "上班"

            if action:
                # 打卡前重新從檔案讀取（防止多個Bot實例重複打卡）
                latest_punched = load_punched_today()
                punched_today.update(latest_punched)
                # 再次確認這張卡還沒打過
                punch_key = f"{uid}-{action}-{today_str}" if action == "in" else (
                    f"{uid}-dutyout-{today_str}" if label == "值班下班" else f"{uid}-out-{today_str}"
                )
                if punch_key in punched_today:
                    action = None
                    label = ""
                # 注意：不在此處寫入 punched_today，等打卡成功後才寫入

            if action:
                scheduled_min_for_action = _t2m(times.get("in" if action == "in" else ("dutyout" if label == "值班下班" else "out"), ""))
                if should_confirm_after_rebind(user_data, today_str, punch_key, scheduled_min_for_action):
                    try:
                        user = client.get_user(int(uid)) or await client.fetch_user(int(uid))
                    except:
                        user = None
                    if user:
                        try:
                            makeup_view = MakeupPunchView(
                                client_ref=client,
                                uid=uid,
                                empid=empid,
                                password=password,
                                action=action,
                                label=label,
                                punch_key=punch_key,
                                punched_today_ref=punched_today,
                                today_str=today_str,
                                retry_key=punch_key,
                            )
                            await user.send(
                                f"📋 **重新綁定後補打確認**\n"
                                f"⚠️ {label}卡排程時間已在本次綁定前經過，系統不會自動補打。\n"
                                f"如確定今天仍需要補打，請按下確認補打。",
                                view=makeup_view
                            )
                        except Exception as e:
                            print(f"重新綁定補打確認通知失敗 {uid}：{e}")
                    mark_rebind_confirm_notified(uid, user_data, punch_key)
                    action = None
                    label = ""

            if action:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda ep=empid, pw=password, ac=action: punch_clock(ep, pw, ac)
                )
                try:
                    user = client.get_user(int(uid)) or await client.fetch_user(int(uid))
                except:
                    user = None

                if user:
                    try:
                        if result.get("success"):
                            # 打卡成功後才寫入 punched_today（避免失敗時誤鎖）
                            punched_today[punch_key] = current_time
                            save_punched_today(punched_today, today_str)
                            msg = f"🤖 自動打卡通知\n✅ {label}打卡成功！（{current_time}）"
                            if result.get("confirmed") and result.get("times"):
                                msg += f"\n📋 e-HR 系統確認：已記錄\n　今日刷卡記錄：{result['times']}"
                            elif result.get("confirmed") is False:
                                msg += "\n📋 e-HR 系統：查無今日刷卡記錄，請至系統確認"
                            await user.send(msg)
                        else:
                            rk = f"{uid}-in-{today_str}" if action == "in" else (
                                f"{uid}-dutyout-{today_str}" if label == "值班下班" else f"{uid}-out-{today_str}"
                            )
                            now_for_retry = datetime.now()
                            retry_at = now_for_retry + timedelta(minutes=RETRY_DELAY_MINUTES)
                            is_in_after_cutoff = action == "in" and (now_for_retry.hour * 60 + now_for_retry.minute) >= PUNCH_IN_CUTOFF_MIN
                            should_retry = not is_in_after_cutoff
                            if should_retry:
                                retry_queue.append({
                                    "uid": uid,
                                    "empid": empid,
                                    "password": password,
                                    "action": action,
                                    "label": label,
                                    "retry_at": retry_at,
                                    "attempts": 0,
                                    "retry_key": rk,
                                })
                                fail_msg = f"🤖 自動打卡通知\n❌ {label}打卡失敗：{result.get('message')}\n⏳ 將於 {RETRY_DELAY_MINUTES} 分鐘後自動重試"
                                alert_status = f"已排入 {RETRY_DELAY_MINUTES} 分鐘後自動重試，最多 {MAX_RETRY_ATTEMPTS} 次"
                            else:
                                fail_msg = f"🤖 自動打卡通知\n❌ {label}打卡失敗：{result.get('message')}\n⚠️ 已達 08:00，為避免遲到記錄，不自動重試上班卡"
                                alert_status = "已達 08:00，不自動補打上班卡，已提醒使用者自行確認"
                            await user.send(fail_msg)
                            await send_admin_alert(
                                client=client,
                                uid=uid,
                                empid=empid,
                                label=label,
                                alert_type="auto_failed",
                                reason=result.get("message", "自動打卡失敗"),
                                status=alert_status,
                                scheduled_time=times.get("in" if action == "in" else ("dutyout" if label == "值班下班" else "out")),
                            )
                            if should_retry and rk:
                                makeup_view = MakeupPunchView(
                                    client_ref=client,
                                    uid=uid,
                                    empid=empid,
                                    password=password,
                                    action=action,
                                    label=label,
                                    punch_key=rk,
                                    punched_today_ref=punched_today,
                                    today_str=today_str,
                                    retry_key=rk,
                                )
                                await user.send(
                                    f"🤖 **補打確認**\n⚠️ {label}卡自動打卡失敗，是否立即補打？\n（也可等待 {RETRY_DELAY_MINUTES} 分鐘後自動重試）",
                                    view=makeup_view
                                )
                    except discord.Forbidden:
                        channel = client.get_channel(NOTIFY_CHANNEL_ID)
                        if channel:
                            await channel.send(f"⚠️ 無法私訊 <@{uid}>，請開啟私訊權限")
                    except Exception as e:
                        print(f"私訊失敗：{e}")
                elif not result.get("success"):
                    await send_admin_alert(
                        client=client,
                        uid=uid,
                        empid=empid,
                        label=label,
                        alert_type="auto_failed",
                        reason=result.get("message", "自動打卡失敗"),
                        status="無法取得使用者私訊對象，管理員需協助確認",
                        scheduled_time=times.get("in" if action == "in" else ("dutyout" if label == "值班下班" else "out")),
                    )

        # ── 處理重試佇列（打卡失敗自動重試，最多3次）──
        now_dt = datetime.now()
        still_retrying = []
        for item in list(retry_queue):
            if now_dt >= item["retry_at"]:
                if item["action"] == "in" and now_dt >= datetime.combine(today, datetime.min.time()) + timedelta(hours=8):
                    try:
                        retry_user = client.get_user(int(item["uid"])) or await client.fetch_user(int(item["uid"]))
                        await retry_user.send(
                            f"🤖 重試打卡通知\n⚠️ {item['label']}卡已超過 08:00，為避免遲到記錄，不再自動重試\n請自行確認上班打卡狀況"
                        )
                        await send_admin_alert(
                            client=client,
                            uid=item["uid"],
                            empid=item["empid"],
                            label=item["label"],
                            alert_type="retry_stopped_cutoff",
                            reason="上班卡重試時間已達 08:00",
                            status="停止自動補打，已提醒使用者自行確認",
                            scheduled_time=scheduled_times.get(item["uid"], {}).get("in"),
                        )
                    except Exception as e:
                        print(f"重試通知失敗：{e}")
                    continue

                retry_result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda ep=item["empid"], pw=item["password"], ac=item["action"]: punch_clock(ep, pw, ac)
                )
                try:
                    retry_user = client.get_user(int(item["uid"])) or await client.fetch_user(int(item["uid"]))
                except:
                    retry_user = None
                if retry_user:
                    attempt_no = item["attempts"] + 1
                    attempt_str = f"（第 {attempt_no} 次重試）"
                    rk = item.get("retry_key")
                    try:
                        if retry_result.get("success"):
                            rk_to_save = rk or f"{item['uid']}-in-{today_str}"
                            punched_today[rk_to_save] = datetime.now().strftime("%H:%M")
                            save_punched_today(punched_today, today_str)
                            retry_msg = f"🤖 重試打卡通知 {attempt_str}\n✅ {item['label']}打卡成功！"
                            if retry_result.get("confirmed") and retry_result.get("times"):
                                retry_msg += f"\n📋 e-HR 確認：{retry_result['times']}"
                            await retry_user.send(retry_msg)
                        elif attempt_no < MAX_RETRY_ATTEMPTS:
                            next_retry = now_dt + timedelta(minutes=RETRY_DELAY_MINUTES)
                            if item["action"] == "in" and next_retry >= datetime.combine(today, datetime.min.time()) + timedelta(hours=8):
                                retry_msg = f"🤖 重試打卡通知 {attempt_str}\n❌ 仍然失敗：{retry_result.get('message')}\n⚠️ 下一次重試會超過 08:00，為避免遲到記錄，已停止自動重試上班卡"
                                await send_admin_alert(
                                    client=client,
                                    uid=item["uid"],
                                    empid=item["empid"],
                                    label=item["label"],
                                    alert_type="retry_stopped_cutoff",
                                    reason=f"上班卡重試仍失敗：{retry_result.get('message')}",
                                    status="下一次重試會超過 08:00，已停止自動補打",
                                    scheduled_time=scheduled_times.get(item["uid"], {}).get("in"),
                                )
                            else:
                                still_retrying.append({**item, "retry_at": next_retry, "attempts": attempt_no})
                                retry_msg = f"🤖 重試打卡通知 {attempt_str}\n❌ 仍然失敗：{retry_result.get('message')}\n⏳ 將於 {RETRY_DELAY_MINUTES} 分鐘後再試"
                            await retry_user.send(retry_msg)
                            # 同步發補打按鈕（上班卡僅在 08:00 前發）
                            if rk and not (item["action"] == "in" and datetime.now().hour * 60 + datetime.now().minute >= PUNCH_IN_CUTOFF_MIN):
                                makeup_view = MakeupPunchView(
                                    client_ref=client,
                                    uid=item["uid"],
                                    empid=item["empid"],
                                    password=item["password"],
                                    action=item["action"],
                                    label=item["label"],
                                    punch_key=rk,
                                    punched_today_ref=punched_today,
                                    today_str=today_str,
                                    retry_key=rk,
                                )
                                await retry_user.send(
                                    f"🤖 **補打確認**\n⚠️ {item['label']}卡重試仍失敗，是否立即補打？\n（也可等待下次自動重試）",
                                    view=makeup_view
                                )
                        else:
                            retry_msg = f"🤖 重試打卡通知 {attempt_str}\n❌ 重試 {MAX_RETRY_ATTEMPTS} 次後仍失敗，請手動打卡\n原因：{retry_result.get('message')}"
                            await retry_user.send(retry_msg)
                            await send_admin_alert(
                                client=client,
                                uid=item["uid"],
                                empid=item["empid"],
                                label=item["label"],
                                alert_type="retry_final_failed",
                                reason=retry_result.get("message", "重試後仍失敗"),
                                status=f"已重試 {MAX_RETRY_ATTEMPTS} 次仍失敗，已提醒使用者手動處理",
                                scheduled_time=scheduled_times.get(item["uid"], {}).get("in" if item["action"] == "in" else ("dutyout" if item["label"] == "值班下班" else "out")),
                            )
                            # 最終失敗也發一次補打按鈕（上班卡僅在 08:00 前發）
                            if rk and not (item["action"] == "in" and datetime.now().hour * 60 + datetime.now().minute >= PUNCH_IN_CUTOFF_MIN):
                                makeup_view = MakeupPunchView(
                                    client_ref=client,
                                    uid=item["uid"],
                                    empid=item["empid"],
                                    password=item["password"],
                                    action=item["action"],
                                    label=item["label"],
                                    punch_key=rk,
                                    punched_today_ref=punched_today,
                                    today_str=today_str,
                                    retry_key=rk,
                                )
                                await retry_user.send(
                                    f"🤖 **補打確認**\n⚠️ {item['label']}卡已重試 {MAX_RETRY_ATTEMPTS} 次仍失敗，是否手動補打？",
                                    view=makeup_view
                                )
                    except Exception as e:
                        print(f"重試通知失敗：{e}")
            else:
                still_retrying.append(item)
        retry_queue.clear()
        retry_queue.extend(still_retrying)

        # ── 打卡前 10 分鐘提醒 ──
        for uid_pre, ud_pre in data.items():
            if not ud_pre.get("empid") or not ud_pre.get("auto_punch", True):
                continue
            if not ud_pre.get("notify", {}).get("pre_punch", True):
                continue
            if is_auto_cancelled(ud_pre, today):
                continue
            times_pre = scheduled_times.get(uid_pre, {})
            in_t_pre = times_pre.get("in", "")
            out_t_pre = times_pre.get("out", "")
            duty_t_pre = times_pre.get("dutyout", "")

            # 計算10分鐘前的時間
            def minus_10(t):
                if not t:
                    return ""
                try:
                    h, m = int(t[:2]), int(t[3:])
                    total = h * 60 + m - 10
                    if total < 0:
                        return ""
                    return f"{total // 60:02d}:{total % 60:02d}"
                except:
                    return ""

            remind_msgs = []
            # 上班前10分鐘（值班隔天不提醒，休假不提醒，週末非值班不提醒）
            if current_time == minus_10(in_t_pre):
                if (not is_leave_day(ud_pre, today)
                        and not is_duty_day(ud_pre, yesterday)
                        and not (is_weekend(today) and not is_duty_day(ud_pre, today))):
                    key_pre = f"{uid_pre}-in-{today_str}"
                    if key_pre not in punched_today:
                        remind_msgs.append(f"⏰ 提醒：即將在 **{in_t_pre}** 自動打**上班卡**")
            # 下班前10分鐘（值班當天不提醒、值班隔天不提醒、平日非值班才提醒）
            if current_time == minus_10(out_t_pre):
                if (not is_duty_day(ud_pre, today)
                        and not is_duty_day(ud_pre, yesterday)
                        and not is_weekend(today)
                        and not is_leave_day(ud_pre, today)):
                    key_pre = f"{uid_pre}-out-{today_str}"
                    if key_pre not in punched_today:
                        remind_msgs.append(f"⏰ 提醒：即將在 **{out_t_pre}** 自動打**下班卡**")
            # 值班下班前10分鐘（值班隔天才提醒）
            if current_time == minus_10(duty_t_pre):
                if is_duty_day(ud_pre, yesterday):
                    key_pre = f"{uid_pre}-dutyout-{today_str}"
                    if key_pre not in punched_today:
                        remind_msgs.append(f"⏰ 提醒：即將在 **{duty_t_pre}** 自動打**值班下班卡**")

            if remind_msgs:
                try:
                    pre_user = client.get_user(int(uid_pre)) or await client.fetch_user(int(uid_pre))
                    await pre_user.send("\n".join(remind_msgs))
                except Exception as e:
                    print(f"打卡前提醒失敗 {uid_pre}：{e}")

        # ── 每日早上 06:50 推送今日排程提醒 ──
        if current_time == "06:50":
            data_for_dm = load_data()
            for uid_dm, ud_dm in data_for_dm.items():
                if not ud_dm.get("empid") or not ud_dm.get("auto_punch", True):
                    continue
                if not ud_dm.get("notify", {}).get("morning", True):
                    continue
                if is_leave_day(ud_dm, today) and not is_duty_day(ud_dm, today - timedelta(days=1)):
                    continue
                if is_auto_cancelled(ud_dm, today):
                    continue
                times_dm = scheduled_times.get(uid_dm, {})
                in_t = times_dm.get("in", f"{PUNCH_IN_START[0]:02d}:{PUNCH_IN_START[1]:02d}~{PUNCH_IN_END[0]:02d}:{PUNCH_IN_END[1]:02d}")
                out_t = times_dm.get("out", f"{PUNCH_OUT_START[0]:02d}:{PUNCH_OUT_START[1]:02d}~{PUNCH_OUT_END[0]:02d}:{PUNCH_OUT_END[1]:02d}")
                duty_t = times_dm.get("dutyout", f"{DUTY_OUT_START[0]:02d}:{DUTY_OUT_START[1]:02d}~{DUTY_OUT_END[0]:02d}:{DUTY_OUT_END[1]:02d}")
                yesterday_dm = today - timedelta(days=1)
                if is_weekend(today) and not is_duty_day(ud_dm, today) and not is_duty_day(ud_dm, yesterday_dm):
                    schedule_msg = "📴 今日為週末，不自動打卡"
                elif is_duty_day(ud_dm, yesterday_dm):
                    schedule_msg = f"🌙 今日為值班隔天\n⏰ 值班下班卡：**{duty_t}**\n（不打上班卡）"
                elif is_duty_day(ud_dm, today):
                    schedule_msg = f"🌙 今日值班\n⏰ 上班卡：**{in_t}**\n⏰ 值班下班（明天）：**{duty_t}**"
                else:
                    schedule_msg = f"🟢 今日平日\n⏰ 上班卡：**{in_t}**\n⏰ 下班卡：**{out_t}**"
                dm_msg = f"🤖 **今日打卡排程提醒**\n{schedule_msg}\n\n✅ Bot 運作正常 · 時間在範圍內隨機產生"
                try:
                    dm_user = client.get_user(int(uid_dm)) or await client.fetch_user(int(uid_dm))
                    await dm_user.send(dm_msg)
                except Exception as e:
                    print(f"早晨提醒私訊失敗 {uid_dm}：{e}")

        # ── 18:00 平日打卡時間比對通知 ──
        if current_time == "18:00":
            data_check = load_data()
            for uid_c, ud_c in data_check.items():
                empid_c = ud_c.get("empid")
                password_c = ud_c.get("password")
                if not empid_c or not password_c or not ud_c.get("auto_punch", True):
                    continue
                if not ud_c.get("notify", {}).get("compare", True):
                    continue
                if not expects_regular_out_check(ud_c, today):
                    continue
                try:
                    def do_check(ep=empid_c, pw=password_c):
                        return _query_today_from_monthly_b9(ep, pw)
                    result_c = await asyncio.get_event_loop().run_in_executor(None, do_check)
                    inferred_c = infer_punch_times(result_c, False) if result_c.get("success") else {"inferred_out": None, "out_source": None}
                    eff_out_c = inferred_c["inferred_out"]
                    out_src_c = inferred_c["out_source"]

                    # ① e-HR index6 有值，或 ② times 推算有結果 → 不通知
                    if eff_out_c:
                        pass
                    else:
                        # ③④ 無刷卡記錄 → 發通知 + 補打按鈕
                        punched_c = load_punched_today()  # 只讀一次，下方直接傳入
                        rk_c = f"{uid_c}-out-{today_str}"
                        if rk_c in punched_c:
                            pt = punched_c[rk_c]
                            notify_msg = f"📋 **今日下班打卡確認（18:00）**\n⚠️ Bot 已在 {pt} 打卡，但 e-HR 尚未記錄到刷卡\n請確認是否需要補打"
                            alert_reason_c = "Bot 已送出下班卡，但 e-HR 尚未記錄"
                        else:
                            notify_msg = f"📋 **今日下班打卡確認（18:00）**\n⚠️ 未偵測到任何下班打卡記錄\n請確認是否需要補打"
                            alert_reason_c = "未偵測到任何下班打卡記錄"
                        await send_admin_alert(
                            client=client,
                            uid=uid_c,
                            empid=empid_c,
                            label="下班",
                            alert_type="ehr_missing_1800",
                            reason=alert_reason_c,
                            status="已通知使用者並發送補打按鈕",
                            scheduled_time=scheduled_times.get(uid_c, {}).get("out"),
                        )
                        check_user = client.get_user(int(uid_c)) or await client.fetch_user(int(uid_c))
                        await check_user.send(notify_msg)
                        makeup_view_c = MakeupPunchView(
                            client_ref=client,
                            uid=uid_c,
                            empid=empid_c,
                            password=password_c,
                            action="out",
                            label="下班",
                            punch_key=rk_c,
                            punched_today_ref=punched_c,
                            today_str=today_str,
                            retry_key=rk_c,
                        )
                        await check_user.send("是否立即補打下班卡？", view=makeup_view_c)
                except Exception as e:
                    print(f"18:00 打卡比對失敗 {uid_c}：{e}")

        # ── 09:00 值班隔天打卡時間比對通知 ──
        if current_time == "09:00":
            data_check2 = load_data()
            for uid_c2, ud_c2 in data_check2.items():
                empid_c2 = ud_c2.get("empid")
                password_c2 = ud_c2.get("password")
                if not empid_c2 or not password_c2:
                    continue
                if not ud_c2.get("notify", {}).get("compare", True):
                    continue
                if not expects_dutyout_check(ud_c2, today):
                    continue
                try:
                    def do_check2(ep=empid_c2, pw=password_c2):
                        return _query_today_from_monthly_b9(ep, pw)
                    result_c2 = await asyncio.get_event_loop().run_in_executor(None, do_check2)
                    inferred_c2 = infer_punch_times(result_c2, True) if result_c2.get("success") else {"inferred_out": None, "out_source": None}
                    eff_out_c2 = inferred_c2["inferred_out"]

                    # ① index6 有值，或 ② times ≥08:00 推算有結果 → 不通知
                    if eff_out_c2:
                        pass
                    else:
                        # ③④ 無刷卡記錄 → 發通知 + 補打按鈕
                        punched_c2 = load_punched_today()  # 只讀一次
                        rk_c2 = f"{uid_c2}-dutyout-{today_str}"
                        if rk_c2 in punched_c2:
                            pt2 = punched_c2[rk_c2]
                            notify_msg2 = f"📋 **今日值班下班打卡確認（09:00）**\n⚠️ Bot 已在 {pt2} 打卡，但 e-HR 尚未記錄到刷卡\n請確認是否需要補打"
                            alert_reason_c2 = "Bot 已送出值班下班卡，但 e-HR 尚未記錄"
                        else:
                            notify_msg2 = f"📋 **今日值班下班打卡確認（09:00）**\n⚠️ 未偵測到任何值班下班打卡記錄\n請確認是否需要補打"
                            alert_reason_c2 = "未偵測到任何值班下班打卡記錄"
                        await send_admin_alert(
                            client=client,
                            uid=uid_c2,
                            empid=empid_c2,
                            label="值班下班",
                            alert_type="ehr_missing_0900",
                            reason=alert_reason_c2,
                            status="已通知使用者並發送補打按鈕",
                            scheduled_time=scheduled_times.get(uid_c2, {}).get("dutyout"),
                        )
                        check_user2 = client.get_user(int(uid_c2)) or await client.fetch_user(int(uid_c2))
                        await check_user2.send(notify_msg2)
                        makeup_view_c2 = MakeupPunchView(
                            client_ref=client,
                            uid=uid_c2,
                            empid=empid_c2,
                            password=password_c2,
                            action="out",
                            label="值班下班",
                            punch_key=rk_c2,
                            punched_today_ref=punched_c2,  # 直接傳入已讀取的 dict
                            today_str=today_str,
                            retry_key=rk_c2,
                        )
                        await check_user2.send("是否立即補打值班下班卡？", view=makeup_view_c2)
                except Exception as e:
                    print(f"09:00 值班打卡比對失敗 {uid_c2}：{e}")

        # ── 每週日 03:00 清理 bot.log ──
        if current_time == "03:00" and today.weekday() == 6:
            try:
                log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")
                if os.path.exists(log_path):
                    # 只保留最後 500 行
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        lines_log = f.readlines()
                    if len(lines_log) > 500:
                        with open(log_path, "w", encoding="utf-8") as f:
                            f.writelines(lines_log[-500:])
                        print("🧹 bot.log 已清理，保留最後 500 行")
            except Exception as e:
                print(f"bot.log 清理失敗：{e}")

        # ── 每月最後一天 19:00 後發送本月打卡摘要；錯過整點會補發，成功者不重複 ──
        next_day = today + timedelta(days=1)
        if next_day.month != today.month and current_time >= "19:00":
            target_month_key = month_key(today)
            summary_state = load_monthly_summary_sent_state()
            sent_by_month = summary_state.setdefault("sent", {})
            sent_users = set(sent_by_month.get(target_month_key, []))
            data_summary = load_data()
            changed_summary_state = False
            for uid_s, ud_s in data_summary.items():
                if uid_s in sent_users:
                    continue
                if not ud_s.get("notify", {}).get("monthly", True):
                    continue
                empid_s = ud_s.get("empid")
                password_s = ud_s.get("password")
                if not empid_s or not password_s:
                    continue
                try:
                    summary_result = await asyncio.get_event_loop().run_in_executor(
                        None, lambda ep=empid_s, pw=password_s, ud=ud_s: _query_monthly_summary(ep, pw, ud)
                    )
                    try:
                        summary_user = client.get_user(int(uid_s)) or await client.fetch_user(int(uid_s))
                        await summary_user.send(summary_result)
                        sent_users.add(uid_s)
                        sent_by_month[target_month_key] = sorted(sent_users)
                        changed_summary_state = True
                        save_monthly_summary_sent_state(summary_state)
                        print(f"月底摘要已發送 {uid_s}：{target_month_key}")
                    except Exception as e:
                        print(f"月底摘要私訊失敗 {uid_s}：{e}")
                except Exception as e:
                    print(f"月底摘要查詢失敗 {uid_s}：{e}")
            if changed_summary_state:
                save_monthly_summary_sent_state(summary_state)

        await asyncio.sleep(60)

# =====================
# 本月 B9 查詢（異常 + 摘要共用）
# =====================
def _query_monthly_b9(empid, password):
    """登入並查詢本月 B9 考勤彙總表，回傳 HTML"""
    from html import unescape
    sess = requests.Session()
    try:
        sess.get(f"{PUNCH_URL}?file={FILE_PARAM}", timeout=10, verify=False)
        sess.post(PUNCH_URL, data={
            "file": FILE_PARAM, "uid": empid, "pwd": password,
            "image.x": "0", "image.y": "0",
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15, verify=False)

        today = date.today()
        roc_year = today.year - 1911
        yymm = f"{roc_year}{today.month:02d}"
        month_last_day = calendar.monthrange(today.year, today.month)[1]
        query_file = "hrm6p_edu.pkg,hrm6p.pkg,hrm6p_out_M1.pkg,hrm6fw_edu.pkg,hrm6bw.pkg,hrm6aw_edu.pkg,hrm6jw_edu.pkg,hrm6p_out_M21.pkg"

        b9_page = sess.get(
            f"{PUNCH_URL}?file={query_file}&init_func=B9.%E8%80%83%E5%8B%A4%E5%BD%99%E7%B8%BD%E8%A1%A8.",
            timeout=10, verify=False
        )
        b9_html = unescape(b9_page.text)
        enc_match = re.search(r'name=enc\s+value="([^"]+)"', b9_html)
        enc = enc_match.group(1) if enc_match else ""

        payload = {
            "time": str(int(time.time() * 1000)),
            "form_ajax": "1",
            "encodeURIComponent": "1",
            "FUNCTION_NAME": "B9.考勤彙總表.",
            "act": "", "init_func": "", "flow_approve": "",
            "buttonid": "button2",
            "buttonlink": "Entry View",
            "fromlink": "Entry View",
            "table_data": "", "form_target": "",
            "menu_display_flag": "",
            "em_step": "0", "em_POSITION": "1",
            "file": query_file,
            "enc": enc,
            "user_id": empid,
            "CONDITION_B": f"+and+a.EMPID%3D%27{empid}%27",
            "showNOTCARD": "N",
            "YYMM": yymm,
            "SKIND": "B",
            "SDATE": f"{roc_year}{today.month:02d}01",
            "EDATE": f"{roc_year}{today.month:02d}{month_last_day:02d}",
            "EMPID": empid,
        }
        resp = sess.post(PUNCH_URL, data=payload, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{PUNCH_URL}?file={query_file}",
            "X-Requested-With": "XMLHttpRequest",
        }, timeout=15, verify=False)
        # 用 raw bytes 手動解碼，伺服器宣稱 utf-8 但實際可能是 Big5/CP950
        raw_bytes = resp.content
        raw = None
        for enc in ("utf-8", "big5", "cp950", "utf-8-sig"):
            try:
                raw = raw_bytes.decode(enc)
                if "考勤" in raw or "員工編號" in raw or "刷卡" in raw:
                    break
            except Exception:
                continue
        if raw is None:
            raw = raw_bytes.decode("utf-8", errors="replace")

        values = re.findall(r'<value>(.*?)</value>', raw, re.DOTALL)
        if values:
            return unescape("".join(values))
        return unescape(raw)
    except Exception:
        return ""

def _date_to_roc(check_date):
    return f"{check_date.year - 1911:03d}/{check_date.month:02d}/{check_date.day:02d}"

def _roc_to_date(roc_date):
    y, m, d = [int(part) for part in roc_date.split("/")]
    return date(y + 1911, m, d)

def _parse_b9_time_cell(value):
    value = (value or "").strip()
    match = re.search(r'(\d{4})', value)
    if not match:
        return None, False
    raw = match.group(1)
    return f"{raw[:2]}:{raw[2:]}", "隔天" in value

def _parse_b9_time_list(value):
    parsed = []
    if not value:
        return parsed
    for item in re.split(r'[,，]', value):
        t, _is_next_day = _parse_b9_time_cell(item)
        if t:
            parsed.append(t)
    return parsed

def _records_by_roc_date(records):
    return {item.get("date"): item for item in records if item.get("date")}

def _record_all_times(record):
    if not record:
        return []
    times = set(record.get("raw_times") or [])
    times.update(record.get("makeup_times") or [])
    if record.get("in"):
        times.add(record["in"])
    if record.get("out"):
        times.add(record["out"])
    return sorted(times)

def _next_day_out_from_previous_record(records_by_date, target_date):
    prev_record = records_by_date.get(_date_to_roc(target_date - timedelta(days=1)))
    if prev_record and prev_record.get("out_next_day") and prev_record.get("out"):
        return prev_record.get("out")
    return None

def _duty_out_for_work_date(records_by_date, work_date):
    record = records_by_date.get(_date_to_roc(work_date))
    if record and record.get("out_next_day") and record.get("out"):
        return record.get("out"), "ehr_next_day"
    next_record = records_by_date.get(_date_to_roc(work_date + timedelta(days=1)))
    next_out = next_record.get("out") if next_record else None
    duty_start_min = DUTY_OUT_START[0] * 60 + DUTY_OUT_START[1]
    duty_end_min = DUTY_OUT_END[0] * 60 + DUTY_OUT_END[1]
    if next_out and duty_start_min <= _t2m(next_out) <= duty_end_min:
        return next_out, "ehr"
    return None, None

def _parse_monthly_records(html):
    """從 B9 HTML 解析本月每天打卡記錄
    先定位到 TBODY，再逐行解析資料 TR
    欄位順序：部門/員工編號/姓名/日期/班別/刷卡/出卡/異常/請假/加班/刷卡資料/補刷卡
    """
    from html import unescape as _ue
    records = []

    # 先找到 TBODY 範圍，避免抓到外層 TABLE 的 TR
    tbody_match = re.search(r'<TBODY[^>]*>(.*?)</TBODY>', html, re.DOTALL | re.IGNORECASE)
    if not tbody_match:
        return records
    tbody = tbody_match.group(1)

    # 在 TBODY 中找所有 TR
    rows = re.findall(r'<TR[^>]*>(.*?)</TR>', tbody, re.DOTALL | re.IGNORECASE)

    for row in rows:
        date_match = re.search(r'(\d{3}/\d{2}/\d{2})', row)
        if not date_match:
            continue
        roc_date = date_match.group(1)

        # 用 <TD 切割
        td_parts = re.split(r'<TD[^>]*>', row, flags=re.IGNORECASE)
        td_texts = []
        for part in td_parts[1:]:
            text = re.sub(r'<[^>]+>', '', part)
            text = _ue(text).replace('&nbsp;', '').replace('\xa0', '').strip()
            td_texts.append(text)

        if len(td_texts) < 7:
            continue

        shift = td_texts[4].strip() if len(td_texts) > 4 else ""
        raw_in  = td_texts[5] if len(td_texts) > 5 else ""
        raw_out = td_texts[6] if len(td_texts) > 6 else ""
        abnormal = td_texts[7].strip() if len(td_texts) > 7 else ""
        leave_text = td_texts[8].strip() if len(td_texts) > 8 else ""
        raw_times_str = td_texts[10].strip() if len(td_texts) > 10 else ""
        makeup_times_str = td_texts[11].strip() if len(td_texts) > 11 else ""

        clock_in, _in_next_day = _parse_b9_time_cell(raw_in)
        clock_out, out_next_day = _parse_b9_time_cell(raw_out)
        raw_times = _parse_b9_time_list(raw_times_str)
        makeup_times = _parse_b9_time_list(makeup_times_str)

        records.append({
            "date": roc_date,
            "gregorian_date": _roc_to_date(roc_date),
            "shift": shift,
            "in": clock_in,
            "out": clock_out,
            "out_next_day": out_next_day,
            "abnormal": abnormal,
            "leave_text": leave_text,
            "raw_times": raw_times,
            "makeup_times": makeup_times,
        })
    return records

def _query_today_from_monthly_b9(empid, password, target_date=None):
    """Use the B9 monthly table as the source of truth for index 5/6 and raw punches."""
    target_date = target_date or date.today()
    html = _query_monthly_b9(empid, password)
    if not html:
        return {"success": False, "message": "B9 考勤彙總表查詢失敗"}

    records = _parse_monthly_records(html)
    records_by_date = _records_by_roc_date(records)
    roc_date = _date_to_roc(target_date)
    record = records_by_date.get(roc_date)
    next_day_out = _next_day_out_from_previous_record(records_by_date, target_date)
    if not record and not next_day_out:
        return {"success": False, "message": "今日尚無刷卡記錄"}

    clock_in = record.get("in") if record else None
    clock_out = next_day_out or (record.get("out") if record else None)
    clock_out_source = "ehr_next_day" if next_day_out else ("ehr" if clock_out else None)
    times = set(_record_all_times(record))
    if next_day_out:
        times.add(next_day_out)
    if not times:
        return {"success": False, "message": "今日尚無刷卡記錄"}

    return {
        "success": True,
        "times": sorted(times),
        "raw_times": sorted(set(record.get("raw_times") or [])),
        "makeup_times": sorted(set(record.get("makeup_times") or [])),
        "clock_in": clock_in,
        "clock_out": clock_out,
        "clock_out_source": clock_out_source,
        "next_day_out": next_day_out,
        "abnormal": record.get("abnormal", "") if record else "",
        "shift": record.get("shift", "") if record else "",
        "leave_text": record.get("leave_text", "") if record else "",
        "source": "b9",
    }

def _query_monthly_summary(empid, password, user_data=None):
    """查詢月底摘要，回傳格式化字串"""
    today = date.today()
    html = _query_monthly_b9(empid, password)
    if not html:
        return f"🤖 **{today.month}月打卡摘要**\n\n❌ 無法取得 e-HR 資料，請手動確認"
    records = _parse_monthly_records(html)
    if not records:
        return f"🤖 **{today.month}月打卡摘要**\n\n查無本月打卡記錄"
    records_by_date = _records_by_roc_date(records)

    # 取得 Discord 設定的打卡時間範圍（用於比對）
    in_start = f"{PUNCH_IN_START[0]:02d}:{PUNCH_IN_START[1]:02d}"
    in_end   = f"{PUNCH_IN_END[0]:02d}:{PUNCH_IN_END[1]:02d}"
    out_start = f"{PUNCH_OUT_START[0]:02d}:{PUNCH_OUT_START[1]:02d}"
    out_end   = f"{PUNCH_OUT_END[0]:02d}:{PUNCH_OUT_END[1]:02d}"
    duty_start = f"{DUTY_OUT_START[0]:02d}:{DUTY_OUT_START[1]:02d}"
    duty_end   = f"{DUTY_OUT_END[0]:02d}:{DUTY_OUT_END[1]:02d}"

    def in_range(t, s, e):
        """判斷時間字串 t 是否在 s~e 範圍內"""
        if not t:
            return None
        try:
            th, tm = int(t[:2]), int(t[3:])
            sh, sm = int(s[:2]), int(s[3:])
            eh, em = int(e[:2]), int(e[3:])
            return (sh * 60 + sm) <= (th * 60 + tm) <= (eh * 60 + em)
        except:
            return None

    lines = [f"🤖 **{today.year}年{today.month}月 打卡摘要**\n"]
    lines.append(f"📌 Bot 設定範圍　上班 {in_start}~{in_end}　下班 {out_start}~{out_end}\n")
    ok_count = 0
    miss_count = 0
    ignored_count = 0

    def local_duty(check_date):
        return bool(user_data) and is_duty_day(user_data, check_date)

    def local_leave(check_date):
        return bool(user_data) and is_leave_day(user_data, check_date)

    def b9_duty(record):
        return bool(record and (record.get("out_next_day") or "W7" in record.get("shift", "")))

    def is_expected_nonwork(check_date):
        return local_leave(check_date) or (is_weekend(check_date) and not local_duty(check_date))

    for r in records:
        d = r["date"]
        check_date = r.get("gregorian_date") or _roc_to_date(d)
        ci = r["in"]
        co = r["out"]
        abnormal = r.get("abnormal", "")
        all_times = _record_all_times(r)
        duty_today = local_duty(check_date) or b9_duty(r)
        duty_after_today = (
            (bool(user_data) and is_duty_day(user_data, check_date - timedelta(days=1)))
            or b9_duty(records_by_date.get(_date_to_roc(check_date - timedelta(days=1))))
        )

        # 休假或週末非值班屬於不用上班；即使誤刷卡/刷退也不列異常。
        if is_expected_nonwork(check_date) and not duty_today and not duty_after_today:
            ignored_count += 1
            if all_times:
                lines.append(f"ℹ️ {d}　休假/週末非值班，有刷卡紀錄但不列異常")
            else:
                lines.append(f"ℹ️ {d}　休假/週末非值班，無需打卡")
            continue

        # 值班隔天的值班下班已歸到前一天值班日，不重複計為當天缺卡。
        if duty_after_today and not duty_today:
            ignored_count += 1
            if all_times:
                lines.append(f"ℹ️ {d}　值班隔天紀錄已併入前一日值班下班")
            else:
                lines.append(f"ℹ️ {d}　值班隔天，無一般上下班卡")
            continue

        ci_str = ci or "—"
        if duty_today:
            duty_out, duty_out_source = _duty_out_for_work_date(records_by_date, check_date)
            co = duty_out
            co_suffix = "（隔天）" if duty_out_source == "ehr_next_day" else ""
        else:
            co_suffix = ""
        co_str = (co + co_suffix) if co else "—"

        # 判斷是否在範圍內
        ci_ok = in_range(ci, in_start, in_end)
        co_ok = in_range(co, duty_start, duty_end) if duty_today else in_range(co, out_start, out_end)

        # 上班時間標記
        if ci and ci_ok is True:
            ci_label = f"{ci_str}✅"
        elif ci and ci_ok is False:
            ci_label = f"{ci_str}⚠️"
        else:
            ci_label = ci_str

        # 下班時間標記
        if co and co_ok is True:
            co_label = f"{co_str}✅"
        elif co and co_ok is False:
            co_label = f"{co_str}⚠️"
        else:
            co_label = co_str

        if abnormal:
            lines.append(f"⚠️ {d}　上班 {ci_label}　下班 {co_label}　{abnormal}")
            miss_count += 1
        elif duty_today and ci and co:
            lines.append(f"✅ {d}　值班　上班 {ci_label}　值班下班 {co_label}")
            ok_count += 1
        elif duty_today:
            lines.append(f"⚠️ {d}　值班　上班 {ci_label}　值班下班 {co_label}（缺卡）")
            miss_count += 1
        elif ci and co:
            lines.append(f"✅ {d}　上班 {ci_label}　下班 {co_label}")
            ok_count += 1
        elif ci or co:
            lines.append(f"⚠️ {d}　上班 {ci_label}　下班 {co_label}（單次刷卡）")
            miss_count += 1
        else:
            lines.append(f"❌ {d}　無刷卡記錄")
            miss_count += 1

    lines.append(f"\n📊 共 {ok_count} 天正常，{miss_count} 天異常/缺卡，{ignored_count} 天休假/週末/值班隔天略過")
    lines.append(f"✅=在Bot範圍內　⚠️=超出範圍或異常　ℹ️=無需一般打卡")
    return "\n".join(lines)

# =====================
# Discord Bot
# =====================
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

def is_admin_interaction(interaction):
    permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
    return bool(permissions and permissions.administrator)

async def reject_non_admin(interaction):
    if is_admin_interaction(interaction):
        return False
    embed = discord.Embed(title="❌ 權限不足", description="此指令僅限管理員使用", color=0xff0000)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)
    return True

def update_date_setting(user_data, field, raw_dates, mode, month_value=None, source="date_setting"):
    if field not in user_data:
        user_data[field] = []
    effective_month = month_value if month_value is not None else date.today().month
    if mode == "reset":
        target_year, target_month = resolve_month_year(effective_month)
        target_prefix = f"{target_year:04d}-{target_month:02d}-"
        user_data[field] = [
            value for value in user_data[field]
            if not value.startswith(target_prefix)
        ]

    added, removed, failed = [], [], []
    parsed, parse_failed = parse_date_tokens(raw_dates, effective_month)
    failed.extend(parse_failed)

    for date_str, display, _dt in parsed:
        try:
            if mode in ("reset", "add"):
                if date_str not in user_data[field]:
                    user_data[field].append(date_str)
                added.append(display)
            elif mode == "remove":
                if date_str in user_data[field]:
                    user_data[field].remove(date_str)
                    removed.append(display)
                else:
                    failed.append(f"{display}（不在清單）")
        except Exception:
            failed.append(display)

    user_data[field] = sorted(user_data[field])
    next_month_key = month_key_for_next_month()
    if mode in ("reset", "add") and added:
        touched_next_month = any(dt.strftime("%Y-%m") == next_month_key for _, _, dt in parsed)
        if touched_next_month:
            mark_monthly_auto_confirmed(user_data, next_month_key, source)
    return added, removed, failed

def build_date_setting_embed(kind, mode, added, removed, failed, target_user=None):
    is_duty = kind == "duty"
    title_map = {
        ("duty", "reset"): "🌙 值班日重設",
        ("duty", "add"): "🌙 新增值班日",
        ("duty", "remove"): "🗑️ 取消值班日",
        ("leave", "reset"): "🏖️ 休假日重設",
        ("leave", "add"): "🏖️ 新增休假日",
        ("leave", "remove"): "🗑️ 取消休假日",
    }
    label = "值班日" if is_duty else "休假日"
    color = 0x9b59b6 if is_duty else 0x3498db
    if mode == "remove":
        color = 0xffaa00

    target_prefix = f"對象：{target_user.mention}\n\n" if target_user else ""
    if mode == "reset":
        desc = target_prefix + f"⚠️ 原有{label}設定已清空，重新設定為：\n\n"
        desc += f"✅ {label}：{', '.join(added)}" if added else f"（無設定任何{label}）"
    elif mode == "add":
        desc = target_prefix
        if added:
            desc += f"✅ 已新增{label}：{', '.join(added)}"
    else:
        desc = target_prefix
        if removed:
            desc += f"✅ 已取消{label}：{', '.join(removed)}\n"

    if is_duty and mode in ("reset", "add") and added:
        desc += "\n\n值班打卡時間：\n⏰ 當天 07:00~07:40 上班卡（隨機）\n⏰ 隔天 08:05~08:40 下班卡（隨機）"
    elif not is_duty and mode in ("reset", "add") and added:
        desc += "\n當天不會自動打卡"

    if failed:
        if mode == "remove":
            desc += f"⚠️ {', '.join(failed)}"
        else:
            example = "6/7 6/14" if is_duty else "6/23 6/24"
            desc += f"\n\n❌ 以下日期格式有誤：{', '.join(failed)}\n格式範例：{example}"

    if not desc.strip():
        desc = target_prefix + "沒有變更任何設定"
    return discord.Embed(title=title_map[(kind, mode)], description=desc, color=color)

admin_group = app_commands.Group(
    name="管理",
    description="管理員專用指令",
    default_permissions=discord.Permissions(administrator=True),
    guild_only=True,
)
tree.add_command(admin_group)

# ── 補打卡確認 View（需使用者按鈕確認才打卡）──
class MakeupPunchView(discord.ui.View):
    def __init__(self, client_ref, uid, empid, password, action, label, punch_key, punched_today_ref, today_str, retry_key=None, suppress_admin_alerts=False):
        super().__init__(timeout=600)  # 10 分鐘內有效
        self.client_ref = client_ref
        self.uid = uid
        self.empid = empid
        self.password = password
        self.action = action
        self.label = label
        self.punch_key = punch_key
        self.punched_today_ref = punched_today_ref
        self.today_str = today_str
        self.retry_key = retry_key  # 對應 retry_queue 的 retry_key，按下補打後清除重試
        self.suppress_admin_alerts = suppress_admin_alerts
        self.done = False

    @discord.ui.button(label='✅ 確認補打', style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            await interaction.response.send_message("已處理過此補打請求。", ephemeral=True)
            return
        self.done = True
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="⏳ 正在補打中...", view=self)
        latest_punched = load_punched_today()
        self.punched_today_ref.update(latest_punched)
        if self.punch_key in self.punched_today_ref:
            if self.retry_key:
                retry_queue[:] = [r for r in retry_queue if r.get("retry_key") != self.retry_key]
            punched_at = self.punched_today_ref.get(self.punch_key) or "稍早"
            await interaction.edit_original_response(
                content=f"🤖 **補打通知**\n✅ {self.label}卡已在 {punched_at} 打卡成功，未重複補打。",
                view=None
            )
            return
        if self.action == "in" and datetime.now().hour * 60 + datetime.now().minute >= PUNCH_IN_CUTOFF_MIN:
            self.processed = True
            if self.retry_key:
                retry_queue[:] = [r for r in retry_queue if r.get("retry_key") != self.retry_key]
            await interaction.edit_original_response(
                content=f"🤖 **補打通知**\n⚠️ {self.label}卡已達 08:00，為避免遲到記錄，不再自動補打。\n請自行確認上班打卡狀況。",
                view=None
            )
            if not self.suppress_admin_alerts:
                await send_admin_alert(
                    client=self.client_ref,
                    uid=self.uid,
                    empid=self.empid,
                    label=self.label,
                    alert_type="makeup_stopped_cutoff",
                    reason="使用者按下補打按鈕時已達 08:00",
                    status="未執行上班補打，已提醒使用者自行確認",
                )
            return

        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: punch_clock(self.empid, self.password, self.action)
        )
        punch_time = datetime.now().strftime('%H:%M')
        if result.get("success"):
            self.punched_today_ref[self.punch_key] = punch_time
            save_punched_today(self.punched_today_ref, self.today_str)
            # 補打成功後從 retry_queue 移除對應重試項目，避免重複打卡
            if self.retry_key:
                retry_queue[:] = [r for r in retry_queue if r.get("retry_key") != self.retry_key]
            msg = f"🤖 **補打通知**\n✅ {self.label}卡補打成功！（{punch_time}）"
            if result.get("confirmed") and result.get("times"):
                msg += f"\n📋 e-HR 確認：{result['times']}"
        else:
            msg = f"🤖 **補打通知**\n❌ {self.label}卡補打失敗：{result.get('message')}\n請手動打卡"
        await interaction.edit_original_response(content=msg, view=None)

    @discord.ui.button(label='❌ 取消', style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            await interaction.response.send_message("已處理過此補打請求。", ephemeral=True)
            return
        self.done = True
        self.stop()
        await interaction.response.edit_message(content="⏭️ 已取消補打，請記得手動打卡。", view=None)

    async def on_timeout(self):
        # 超時未按，提醒使用者
        try:
            user = await self.client_ref.fetch_user(int(self.uid))
            await user.send(f"⚠️ 補打確認逾時（10 分鐘），{self.label}卡未補打，請記得手動打卡。")
        except Exception:
            pass

def build_next_month_settings_complete_embed(result, target_label=None):
    target_line = f"對象：{target_label}\n" if target_label else ""
    return discord.Embed(
        title="✅ 下月設定已完成",
        description=(
            f"{target_line}已確認 **{result['target_month']}** 繼續使用自動打卡。\n\n"
            f"{result['summary']}"
        ),
        color=0x00ff00,
    )

class NextMonthSettingsModal(discord.ui.Modal, title='下月設定'):
    duty_dates = discord.ui.TextInput(
        label='下月值班日期',
        placeholder='例如：1 3 5，沒有可留空',
        required=False,
        style=discord.TextStyle.short,
    )
    leave_dates = discord.ui.TextInput(
        label='下月休假日期',
        placeholder='例如：10 11，沒有可留空',
        required=False,
        style=discord.TextStyle.short,
    )

    def __init__(self, target_uid, actor_uid=None, target_label=None, source="next_month_settings_modal", require_binding=True):
        super().__init__()
        self.target_uid = str(target_uid)
        self.actor_uid = str(actor_uid or target_uid)
        self.target_label = target_label
        self.source = source
        self.require_binding = require_binding

    async def on_submit(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.actor_uid:
            await interaction.response.send_message("這個下月設定按鈕不是給你操作的。", ephemeral=True)
            return
        user_data = get_user_data(self.target_uid)
        if self.require_binding and (not user_data.get("empid") or not user_data.get("password")):
            await interaction.response.send_message("請先使用 `/帳號綁定` 綁定 e-HR 帳號。", ephemeral=True)
            return
        result = apply_next_month_settings(
            user_data,
            duty_dates=str(self.duty_dates.value or ""),
            leave_dates=str(self.leave_dates.value or ""),
            no_special_days=False,
            source=self.source,
        )
        if not result.get("success"):
            await interaction.response.send_message(
                result.get("message", "下月設定未完成。").replace("請選擇「無值班無休假」", "請按「是，下月無值班無休假」"),
                ephemeral=True,
            )
            return
        save_user_data(self.target_uid, user_data)
        await interaction.response.send_message(
            embed=build_next_month_settings_complete_embed(result, self.target_label),
            ephemeral=True,
        )

class NextMonthReminderView(discord.ui.View):
    def __init__(self, target_uid, actor_uid=None, target_label=None, source="next_month_settings", require_binding=True):
        super().__init__(timeout=432000)
        self.target_uid = str(target_uid)
        self.actor_uid = str(actor_uid or target_uid)
        self.target_label = target_label
        self.source = source
        self.require_binding = require_binding

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label='是，下月無值班無休假', style=discord.ButtonStyle.success)
    async def no_special_days(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.actor_uid:
            await interaction.response.send_message("這個下月設定按鈕不是給你操作的。", ephemeral=True)
            return
        user_data = get_user_data(self.target_uid)
        if self.require_binding and (not user_data.get("empid") or not user_data.get("password")):
            await interaction.response.send_message("請先使用 `/帳號綁定` 綁定 e-HR 帳號。", ephemeral=True)
            return
        result = apply_next_month_settings(
            user_data,
            no_special_days=True,
            source=f"{self.source}_button",
        )
        if not result.get("success"):
            await interaction.response.send_message(result.get("message", "下月設定未完成。"), ephemeral=True)
            return
        save_user_data(self.target_uid, user_data)
        self._disable_all()
        target_line = f"對象：{self.target_label}\n" if self.target_label else ""
        await interaction.response.edit_message(
            content=(
                f"✅ **下月設定已完成**\n"
                f"{target_line}已確認 **{result['target_month']}** 繼續使用自動打卡。\n\n"
                f"{result['summary']}"
            ),
            view=self,
        )
        await interaction.followup.send(
            embed=build_next_month_settings_complete_embed(result, self.target_label),
            ephemeral=True,
        )

    @discord.ui.button(label='否，我要填值班/休假', style=discord.ButtonStyle.primary)
    async def open_settings_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.actor_uid:
            await interaction.response.send_message("這個下月設定按鈕不是給你操作的。", ephemeral=True)
            return
        await interaction.response.send_modal(
            NextMonthSettingsModal(
                target_uid=self.target_uid,
                actor_uid=self.actor_uid,
                target_label=self.target_label,
                source=f"{self.source}_modal",
                require_binding=self.require_binding,
            )
        )

class PersistentNextMonthReminderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label='是，下月無值班無休假',
        style=discord.ButtonStyle.success,
        custom_id="next_month:no_special_days",
    )
    async def no_special_days(self, interaction: discord.Interaction, button: discord.ui.Button):
        target_uid = str(interaction.user.id)
        user_data = get_user_data(target_uid)
        if not user_data.get("empid") or not user_data.get("password"):
            await interaction.response.send_message("請先使用 `/帳號綁定` 綁定 e-HR 帳號。", ephemeral=True)
            return
        result = apply_next_month_settings(
            user_data,
            no_special_days=True,
            source="persistent_next_month_settings_button",
        )
        if not result.get("success"):
            await interaction.response.send_message(result.get("message", "下月設定未完成。"), ephemeral=True)
            return
        save_user_data(target_uid, user_data)
        await interaction.response.edit_message(
            content=(
                f"✅ **下月設定已完成**\n"
                f"已確認 **{result['target_month']}** 繼續使用自動打卡。\n\n"
                f"{result['summary']}"
            ),
            view=None,
        )
        await interaction.followup.send(
            embed=build_next_month_settings_complete_embed(result),
            ephemeral=True,
        )

    @discord.ui.button(
        label='否，我要填值班/休假',
        style=discord.ButtonStyle.primary,
        custom_id="next_month:open_modal",
    )
    async def open_settings_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        target_uid = str(interaction.user.id)
        await interaction.response.send_modal(
            NextMonthSettingsModal(
                target_uid=target_uid,
                actor_uid=target_uid,
                source="persistent_next_month_settings_modal",
                require_binding=True,
            )
        )

# ── 手動打卡 ──
class PunchModal(discord.ui.Modal, title='打卡系統'):
    username = discord.ui.TextInput(label='員工編號', placeholder='請輸入員工編號', required=True)
    password = discord.ui.TextInput(label='密碼', placeholder='請輸入密碼', required=True, style=discord.TextStyle.short)

    def __init__(self, action):
        super().__init__()
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: punch_clock(self.username.value, self.password.value, self.action)
            )
            action_text = "上班" if self.action == "in" else "下班"
            if result.get("success"):
                lines = [f"✅ {action_text}打卡成功！"]
                if result.get("confirmed") and result.get("times"):
                    lines.append(f"\n📋 **e-HR 系統確認：** 已記錄")
                    lines.append(f"　今日刷卡記錄：{result['times']}")
                elif result.get("confirmed") is False:
                    lines.append("\n📋 **e-HR 系統：** 查無今日刷卡記錄，請至系統確認")
                embed = discord.Embed(
                    title="✅ 打卡成功",
                    description="\n".join(lines),
                    color=0x00ff00
                )
            else:
                embed = discord.Embed(title="❌ 打卡失敗", description=result.get("message"), color=0xff0000)
        except Exception as e:
            embed = discord.Embed(title="❌ 錯誤", description=str(e), color=0xff0000)
        await interaction.followup.send(embed=embed, ephemeral=True)

class PunchView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='🟢 上班打卡', style=discord.ButtonStyle.success)
    async def punch_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PunchModal(action="in"))

    @discord.ui.button(label='🔴 下班打卡', style=discord.ButtonStyle.danger)
    async def punch_out(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PunchModal(action="out"))

@tree.command(name="打卡", description="手動打卡")
async def punch_command(interaction: discord.Interaction):
    embed = discord.Embed(title="🏥 e-HR 手動打卡", description="請選擇上班或下班打卡", color=0x5865F2)
    await interaction.response.send_message(embed=embed, view=PunchView(), ephemeral=True)

# ── 綁定帳號（先驗證 e-HR 帳密，不執行打卡）──
async def new_user_punch_assessment(discord_user, uid_str, empid, password, user_data, admin_initiated_by=None):
    """
    帳號綁定後立即執行補卡評估，確保當天已過的排程不會漏打。
    在背景以 asyncio.create_task() 執行，不阻塞綁定指令回應。
    """
    import asyncio as _asyncio
    await _asyncio.sleep(2)  # 稍等讓綁定回應先送出

    today = date.today()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    now_dt = datetime.now()
    now_min = now_dt.hour * 60 + now_dt.minute  # 現在幾分鐘

    # 確保排程已產生
    if uid_str not in scheduled_times:
        h_in, m_in   = get_random_punch_time(*PUNCH_IN_START,  *PUNCH_IN_END)
        h_out, m_out = get_random_punch_time(*PUNCH_OUT_START, *PUNCH_OUT_END)
        h_duty, m_duty = get_random_punch_time(*DUTY_OUT_START, *DUTY_OUT_END)
        scheduled_times[uid_str] = {
            "in":      f"{h_in:02d}:{m_in:02d}",
            "out":     f"{h_out:02d}:{m_out:02d}",
            "dutyout": f"{h_duty:02d}:{m_duty:02d}",
        }
        save_schedule_today(scheduled_times, today_str)

    sch = scheduled_times[uid_str]
    def _t2m_local(t):
        try: return int(t[:2]) * 60 + int(t[3:])
        except: return -1

    sch_in_min   = _t2m_local(sch["in"])
    sch_out_min  = _t2m_local(sch["out"])
    sch_duty_min = _t2m_local(sch["dutyout"])

    is_duty_after  = is_duty_day(user_data, yesterday)
    is_duty_today  = is_duty_day(user_data, today)
    is_leave_today = is_leave_day(user_data, today)
    is_wknd        = is_weekend(today)
    is_cancelled_today = is_auto_cancelled(user_data, today)

    # ── 情境判斷 ──
    # 週末非值班、休假非值班隔天 → 不處理
    if is_cancelled_today:
        return
    if (is_wknd and not is_duty_today and not is_duty_after):
        return
    if (is_leave_today and not is_duty_after):
        return

    try:
        # 登入查詢 e-HR
        def do_assess(ep=empid, pw=password):
            return _query_today_from_monthly_b9(ep, pw)

        result = await asyncio.get_event_loop().run_in_executor(None, do_assess)
        inferred = infer_punch_times(result, is_duty_after) if result.get("success") else {
            "inferred_in": None, "inferred_out": None, "in_source": None, "out_source": None
        }
        eff_in  = inferred["inferred_in"]
        eff_out = inferred["inferred_out"]

        msgs = []  # 要發給用戶的訊息（純文字）
        views = [] # (msg, MakeupPunchView) 需要附按鈕的

        # ── 值班隔天流程 ──
        if is_duty_after:
            if now_min < 8 * 60:
                # 08:00 前，等排程自動打
                pass
            else:
                # 08:00 後，查 index6/times ≥08:00
                if eff_out:
                    pass  # 有記錄，不處理
                else:
                    rk = f"{uid_str}-dutyout-{today_str}"
                    views.append((
                        f"📋 **帳號綁定後補卡確認**\n"
                        f"⚠️ 今日為值班隔天，08:00 後尚無值班下班打卡記錄\n"
                        f"是否立即補打值班下班卡？",
                        MakeupPunchView(
                            client_ref=client,
                            uid=uid_str,
                            empid=empid,
                            password=password,
                            action="out",
                            label="值班下班",
                            punch_key=rk,
                            punched_today_ref=load_punched_today(),
                            today_str=today_str,
                            retry_key=rk,
                            suppress_admin_alerts=bool(admin_initiated_by),
                        )
                    ))

        # ── 平日 / 值班日流程 ──
        else:
            # 上班卡評估（值班日和平日邏輯相同）
            if now_min > sch_in_min:
                if now_min < 8 * 60:
                    # 還在 08:00 前，可補打
                    if not eff_in:
                        rk = f"{uid_str}-in-{today_str}"
                        views.append((
                            f"📋 **帳號綁定後補卡確認**\n"
                            f"⚠️ 上班排程（{sch['in']}）已過，e-HR 尚無上班打卡記錄\n"
                            f"是否立即補打上班卡？（仍在 08:00 前，可補）",
                            MakeupPunchView(
                                client_ref=client,
                                uid=uid_str,
                                empid=empid,
                                password=password,
                                action="in",
                                label="上班",
                                punch_key=rk,
                                punched_today_ref=load_punched_today(),
                                today_str=today_str,
                                retry_key=None,
                                suppress_admin_alerts=bool(admin_initiated_by),
                            )
                        ))
                else:
                    # 08:00 後，不補打上班卡
                    if not eff_in:
                        msgs.append(
                            f"📋 **帳號綁定後補卡確認**\n"
                            f"⚠️ 上班排程（{sch['in']}）已過且超過 08:00，無法自動補打上班卡\n"
                            f"請自行確認上班打卡狀況"
                        )

            # 下班卡評估（值班日不打下班卡）
            if not is_duty_today and now_min > sch_out_min:
                if not eff_out:
                    rk = f"{uid_str}-out-{today_str}"
                    views.append((
                        f"📋 **帳號綁定後補卡確認**\n"
                        f"⚠️ 下班排程（{sch['out']}）已過，e-HR 尚無下班打卡記錄\n"
                        f"是否立即補打下班卡？",
                        MakeupPunchView(
                            client_ref=client,
                            uid=uid_str,
                            empid=empid,
                            password=password,
                            action="out",
                            label="下班",
                            punch_key=rk,
                            punched_today_ref=load_punched_today(),
                            today_str=today_str,
                            retry_key=rk,
                            suppress_admin_alerts=bool(admin_initiated_by),
                        )
                    ))

        # ── 發送訊息 ──
        admin_notice_labels = []
        for msg in msgs:
            await discord_user.send(msg)
            admin_notice_labels.append("上班")
        for msg, view in views:
            await discord_user.send(msg, view=view)
            admin_notice_labels.append(view.label)

        if admin_initiated_by and admin_notice_labels:
            schedule_by_label = {
                "上班": sch.get("in"),
                "下班": sch.get("out"),
                "值班下班": sch.get("dutyout"),
            }
            for label in sorted(set(admin_notice_labels)):
                await send_admin_alert(
                    client=client,
                    uid=uid_str,
                    empid=empid,
                    label=label,
                    alert_type="admin_bind_assessment",
                    reason=f"管理員 <@{admin_initiated_by}> 今日代為綁定後，系統觸發使用者補打/確認提醒",
                    status="已通知使用者；後續補打或重試不因本次代綁定額外通知管理員",
                    scheduled_time=schedule_by_label.get(label),
                )

    except Exception as e:
        print(f"補卡評估失敗 {uid_str}：{e}")


class BindModal(discord.ui.Modal, title='綁定打卡帳號'):
    empid = discord.ui.TextInput(label='員工編號', placeholder='請輸入員工編號', required=True)
    password = discord.ui.TextInput(label='密碼', placeholder='請輸入密碼', required=True, style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            empid = self.empid.value.strip()
            password = self.password.value.strip()
            validation = await asyncio.get_event_loop().run_in_executor(
                None, lambda: validate_ehr_login(empid, password)
            )
            if not validation.get("success"):
                embed = discord.Embed(
                    title="❌ 綁定失敗",
                    description=f"{validation.get('message')}\n\n請確認員工編號、密碼與 e-HR 連線後再重新綁定。",
                    color=0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            user_data = get_user_data(interaction.user.id)
            user_data["empid"] = empid
            user_data["password"] = password
            enable_auto_punch(user_data)
            mark_rebind_confirm_only(user_data)
            save_user_data(interaction.user.id, user_data)
            embed = discord.Embed(
                title="✅ 綁定成功",
                description=(
                    f"員工編號 **{empid}** 已綁定，e-HR 帳密驗證成功！\n\n"
                    "📋 預設設定：\n"
                    "• 模式：平日（週一至週五）\n"
                    "• 自動打卡：開啟\n"
                    f"• 上班卡：07:00~07:40 隨機\n"
                    f"• 下班卡：17:05~17:40 隨機\n"
                    "• 週六日：不自動打卡\n\n"
                    "如有值班請使用 `/值班休假設定` 設定\n\n"
                    "🔍 正在確認今日打卡狀況。\n"
                    "⚠️ 本次綁定前已過的排程不會自動補打；如需補打，會私訊請你按鈕確認。"
                ),
                color=0x00ff00
            )
            # 綁定成功後在背景執行補卡評估
            uid_str = str(interaction.user.id)
            asyncio.create_task(new_user_punch_assessment(
                discord_user=interaction.user,
                uid_str=uid_str,
                empid=empid,
                password=password,
                user_data=user_data,
            ))
        except Exception as e:
            embed = discord.Embed(title="❌ 錯誤", description=str(e), color=0xff0000)
        await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="帳號綁定", description="綁定員工帳號以使用自動打卡")
async def bind_command(interaction: discord.Interaction):
    await interaction.response.send_modal(BindModal())

@tree.command(name="帳號解除", description="取消綁定帳號")
async def unbind_command(interaction: discord.Interaction):
    user_data = get_user_data(interaction.user.id)
    user_data["empid"] = None
    user_data["password"] = None
    user_data["auto_punch"] = False
    save_user_data(interaction.user.id, user_data)
    embed = discord.Embed(title="✅ 已解除綁定", description="帳號已解除，自動打卡停止", color=0xffaa00)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="下月設定", description="設定下個月是否繼續自動打卡與休假值班日")
async def next_month_settings(interaction: discord.Interaction):
    user_data = get_user_data(interaction.user.id)
    if not user_data.get("empid") or not user_data.get("password"):
        embed = discord.Embed(
            title="❌ 尚未綁定帳號",
            description="請先使用 `/帳號綁定` 綁定 e-HR 帳號。",
            color=0xff0000,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    target_month = month_key_for_next_month()
    embed = discord.Embed(
        title="📌 下月設定",
        description=(
            f"請選擇 **{target_month}** 的下月設定方式。\n\n"
            "如果下個月沒有值班也沒有休假，請按「是」。\n"
            "如果要填值班或休假日期，請按「否」。\n\n"
            f"{format_month_schedule_summary(user_data, target_month)}"
        ),
        color=0x5865F2,
    )
    await interaction.response.send_message(
        embed=embed,
        view=PersistentNextMonthReminderView(),
        ephemeral=True,
    )

# ── 模式切換 ──
SCHEDULE_KIND_CHOICES = [
    app_commands.Choice(name="值班", value="duty"),
    app_commands.Choice(name="休假", value="leave"),
]

SCHEDULE_ACTION_CHOICES = [
    app_commands.Choice(name="設定（重設整月）", value="reset"),
    app_commands.Choice(name="新增", value="add"),
    app_commands.Choice(name="取消", value="remove"),
]

SCHEDULE_FIELD_BY_KIND = {
    "duty": "duty_days",
    "leave": "leave_dates",
}

SCHEDULE_SOURCE_BY_KIND = {
    "duty": "duty_setting",
    "leave": "leave_setting",
}


@tree.command(name="值班休假設定", description="設定、新增或取消指定月份的值班/休假日期")
@app_commands.describe(
    類型="選擇要調整值班或休假",
    動作="選擇設定、新增或取消",
    月份="要調整的月份，省略則使用本月",
    日期="輸入日期，例如：1 3 5 或 7/1 7/3",
)
@app_commands.choices(類型=SCHEDULE_KIND_CHOICES, 動作=SCHEDULE_ACTION_CHOICES)
async def duty_leave_setting(
    interaction: discord.Interaction,
    類型: str,
    動作: str,
    日期: str,
    月份: int = None,
):
    user_data = get_user_data(interaction.user.id)
    field = SCHEDULE_FIELD_BY_KIND[類型]
    source = SCHEDULE_SOURCE_BY_KIND[類型]
    added, removed, failed = update_date_setting(
        user_data, field, 日期, 動作, 月份, source
    )
    save_user_data(interaction.user.id, user_data)
    embed = build_date_setting_embed(類型, 動作, added, removed, failed)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── 自動打卡控制 ──
@tree.command(name="自動打卡取消", description="取消今天的自動打卡（僅今天，明天自動恢復）")
async def cancel_today(interaction: discord.Interaction):
    user_data = get_user_data(interaction.user.id)
    today_str = date.today().strftime("%Y-%m-%d")
    if today_str not in user_data.get("cancel_dates", []):
        user_data["cancel_dates"].append(today_str)
    save_user_data(interaction.user.id, user_data)
    embed = discord.Embed(
        title="⏸️ 今日自動打卡已取消",
        description="今天將不會自動打卡\n請記得使用 `/打卡` 手動打卡",
        color=0xffaa00
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="自動打卡恢復", description="重新開啟自動打卡")
async def resume_auto(interaction: discord.Interaction):
    user_data = get_user_data(interaction.user.id)
    today_str = date.today().strftime("%Y-%m-%d")
    if today_str in user_data.get("cancel_dates", []):
        user_data["cancel_dates"].remove(today_str)
    enable_auto_punch(user_data)
    save_user_data(interaction.user.id, user_data)
    embed = discord.Embed(
        title="▶️ 自動打卡已恢復",
        description="自動打卡重新開啟！",
        color=0x00ff00
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── 查詢 ──
@tree.command(name="查今日狀態", description="查看今天的打卡模式和時間")
async def today_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)

    user_data = get_user_data(interaction.user.id)
    today = date.today()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    weekday_names = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    weekday = weekday_names[today.weekday()]
    date_str = today.strftime(f"%Y年%m月%d日（{weekday}）")
    uid = str(interaction.user.id)
    empid = user_data.get("empid")
    password = user_data.get("password")

    # 先取得今天的排程時間
    if uid not in scheduled_times and empid:
        h_in, m_in = get_random_punch_time(*PUNCH_IN_START, *PUNCH_IN_END)
        h_out, m_out = get_random_punch_time(*PUNCH_OUT_START, *PUNCH_OUT_END)
        h_duty, m_duty = get_random_punch_time(*DUTY_OUT_START, *DUTY_OUT_END)
        scheduled_times[uid] = {
            "in": f"{h_in:02d}:{m_in:02d}",
            "out": f"{h_out:02d}:{m_out:02d}",
            "dutyout": f"{h_duty:02d}:{m_duty:02d}",
        }
        save_schedule_today(scheduled_times, today.strftime("%Y-%m-%d"))
    user_schedule = scheduled_times.get(uid, {})

    # 基本狀態（不需要查 e-HR）
    if not empid:
        schedule = get_today_schedule(user_data)
        embed = discord.Embed(title=f"📋 今日打卡狀態｜{date_str}", description=schedule, color=0x5865F2)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if is_auto_cancelled(user_data, today):
        embed = discord.Embed(title=f"📋 今日打卡狀態｜{date_str}", description="⏸️ 今日已取消自動打卡（手動模式）", color=0xffaa00)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if not user_data.get("auto_punch", True) and not is_duty_day(user_data, yesterday):
        reason = "⚠️ 自動打卡已關閉\n請使用 `/自動打卡恢復` 開啟"
        if auto_disabled_by_monthly(user_data, today):
            reason = "⚠️ 本月尚未完成續用確認，自動打卡已關閉\n請使用 `/自動打卡恢復` 或聯絡管理員"
        embed = discord.Embed(title=f"📋 今日打卡狀態｜{date_str}", description=reason, color=0xffaa00)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if is_leave_day(user_data, today) and not is_duty_day(user_data, yesterday):
        embed = discord.Embed(title=f"📋 今日打卡狀態｜{date_str}", description="🏖️ 今日為休假日，不會自動打卡", color=0xffaa00)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # 查詢 e-HR 實際刷卡記錄
    loop = asyncio.get_event_loop()
    def do_status_query():
        return _query_today_from_monthly_b9(empid, password, today)

    result = await loop.run_in_executor(None, do_status_query)

    # ── 四線優先順序推算 ──
    is_duty_after = is_duty_day(user_data, yesterday)
    inferred = infer_punch_times(result, is_duty_after) if result.get("success") else {
        "inferred_in": None, "inferred_out": None, "in_source": None, "out_source": None
    }
    eff_in      = inferred["inferred_in"]
    eff_out     = inferred["inferred_out"]
    in_source   = inferred["in_source"]   # "ehr" | "times" | None
    out_source  = inferred["out_source"]  # "ehr" | "times" | None

    # ③ punched_today（e-HR 完全未更新時的 Bot 本地記錄）
    punched_now = load_punched_today()
    uid_str = str(interaction.user.id)
    key_in      = f"{uid_str}-in-{today_str}"
    key_out_p   = f"{uid_str}-out-{today_str}"
    key_duty_p  = f"{uid_str}-dutyout-{today_str}"
    punched_in_t   = punched_now.get(key_in)    if key_in    in punched_now else None
    punched_out_t  = punched_now.get(key_out_p) if key_out_p  in punched_now else None
    punched_duty_t = punched_now.get(key_duty_p) if key_duty_p in punched_now else None

    # ── 組合顯示內容 ──
    lines = []
    scheduled_in   = user_schedule.get("in", "")
    scheduled_out  = user_schedule.get("out", "")
    scheduled_duty = user_schedule.get("dutyout", "")

    # 今天是哪種模式
    if is_duty_after:
        lines.append("🌙 今日為值班隔天")
    elif is_weekend(today) and is_duty_day(user_data, today):
        lines.append("🌙 今日為週末值班日")
    elif is_duty_day(user_data, today):
        lines.append("🌙 今日為值班日")
    elif is_weekend(today):
        lines.append("📴 今日為週末")
    else:
        lines.append("🟢 今日為平日")
    lines.append("")

    # ── 上班欄 ──
    if is_duty_after:
        lines.append("🌙 值班隔天，不打上班卡")
    elif is_leave_day(user_data, today):
        lines.append("🏖️ 今日休假，不打上班卡")
    elif is_weekend(today) and not is_duty_day(user_data, today):
        lines.append("📴 週末不打上班卡")
    elif eff_in and in_source == "ehr":
        lines.append(f"✅ 已在 **{eff_in}** 打卡上班")
    elif eff_in and in_source == "times":
        lines.append(f"✅ 已在 **{eff_in}** 打卡上班\n　（e-HR 有記錄但刷卡/出卡時間待定）")
    elif punched_in_t is not None:
        t = punched_in_t if punched_in_t else scheduled_in
        lines.append(f"✅ 已在 **{t}** 打卡上班\n　（此為自動打卡預設時間，e-HR 尚未記錄）")
    else:
        lines.append("⏳ 尚未打卡上班")
        if scheduled_in:
            lines.append(f"　↳ 預計 **{scheduled_in}** 自動打上班卡")

    # ── 下班欄 ──
    if is_duty_day(user_data, today):
        lines.append("⏳ 今天不打下班卡")
        lines.append(f"　↳ 明天 **{scheduled_duty or '08:05~08:40'}** 自動打值班下班卡")
    elif is_duty_after:
        if eff_out and out_source in ("ehr", "ehr_next_day"):
            lines.append(f"✅ 已在 **{eff_out}** 打卡值班下班\n　（{fmt_source(out_source, is_in=False)}）")
        elif eff_out and out_source == "times":
            lines.append(f"✅ 已在 **{eff_out}** 打卡值班下班\n　（e-HR 有記錄但刷卡/出卡時間待定）")
        elif punched_duty_t is not None:
            t = punched_duty_t if punched_duty_t else scheduled_duty
            lines.append(f"✅ 已在 **{t}** 打卡值班下班\n　（此為自動打卡預設時間，e-HR 尚未記錄）")
        else:
            lines.append("⏳ 尚未打卡值班下班")
            if scheduled_duty:
                lines.append(f"　↳ 預計 **{scheduled_duty}** 自動打值班下班卡")
    elif is_weekend(today):
        lines.append("📴 週末不打下班卡")
    else:
        if eff_out and out_source in ("ehr", "ehr_next_day"):
            lines.append(f"✅ 已在 **{eff_out}** 打卡下班\n　（{fmt_source(out_source, is_in=False)}）")
        elif eff_out and out_source == "times":
            lines.append(f"✅ 已在 **{eff_out}** 打卡下班\n　（e-HR 有記錄但刷卡/出卡時間待定）")
        elif punched_out_t is not None:
            t = punched_out_t if punched_out_t else scheduled_out
            lines.append(f"✅ 已在 **{t}** 打卡下班\n　（此為自動打卡預設時間，e-HR 尚未記錄）")
        else:
            lines.append("⏳ 尚未打卡下班")
            if scheduled_out:
                lines.append(f"　↳ 預計 **{scheduled_out}** 自動打下班卡")

    # ── Bot 排程時間 ──
    lines.append("")
    lines.append("⏰ **今日 Bot 排程打卡時間**")
    if is_weekend(today) and not is_duty_day(user_data, today) and not is_duty_after:
        lines.append("　📴 週末不自動打卡")
    else:
        if not is_duty_day(user_data, today) and not is_weekend(today) and not is_duty_after:
            lines.append(f"　上班卡：**{scheduled_in}**　下班卡：**{scheduled_out}**")
        elif is_duty_day(user_data, today):
            lines.append(f"　上班卡：**{scheduled_in}**　值班下班（明天）：**{scheduled_duty}**")
        if is_duty_after:
            lines.append(f"　值班下班卡（昨日值班）：**{scheduled_duty}**")

    # ── 比對欄 ──
    def _match_line(label, bot_t, actual_t, source):
        if not bot_t or not actual_t:
            return None
        if bot_t == actual_t:
            match_str = "✅ 相符"
            suffix = " （e-HR 有記錄但刷卡/出卡時間待定）" if source == "times" else ""
            return f"🤖 {label}：Bot {bot_t} ＝ e-HR 紀錄 {actual_t} {match_str}{suffix}"
        else:
            # 不符：中間用不等號，並依來源加備註
            if source in ("ehr", "ehr_next_day"):
                note = "（e-HR 已記錄，不用擔心）"
            elif source == "times":
                note = "（e-HR 有記錄但刷卡/出卡時間待定）"
            else:
                note = ""
            return f"🤖 {label}：Bot {bot_t} ≠ e-HR 紀錄 {actual_t} ⚠️ 不符\n　{note}"

    match_lines = []
    if is_duty_after:
        if eff_out:
            ml = _match_line("值班下班", scheduled_duty, eff_out, out_source)
            if ml:
                match_lines.append(ml)
        elif punched_duty_t is not None:
            t = punched_duty_t if punched_duty_t else scheduled_duty
            match_lines.append(f"🤖 值班下班：Bot {t} 刷卡，但 e-HR 尚未記錄")
        else:
            match_lines.append("🤖 值班下班：⚠️ e-HR 尚未記錄到刷卡")
    else:
        if eff_in:
            ml = _match_line("上班卡", scheduled_in, eff_in, in_source)
            if ml:
                match_lines.append(ml)
        elif punched_in_t is not None:
            t = punched_in_t if punched_in_t else scheduled_in
            match_lines.append(f"🤖 上班卡：Bot {t} 刷卡，但 e-HR 尚未記錄")
        if eff_out:
            ml = _match_line("下班卡", scheduled_out, eff_out, out_source)
            if ml:
                match_lines.append(ml)
        elif punched_out_t is not None:
            t = punched_out_t if punched_out_t else scheduled_out
            match_lines.append(f"🤖 下班卡：Bot {t} 刷卡，但 e-HR 尚未記錄")

    if match_lines:
        lines.append("")
        lines.extend(match_lines)

    # ── 通知設定 ──
    notify = user_data.get("notify", {})
    def ns(key): return "✅" if notify.get(key, True) else "❌"
    lines.append("")
    lines.append(f"🔔 **通知設定**　早晨提醒 {ns('morning')}　打卡前提醒 {ns('pre_punch')}　下班比對 {ns('compare')}　月底摘要 {ns('monthly')}")

    embed = discord.Embed(
        title=f"📋 今日打卡狀態｜{date_str}",
        description="\n".join(lines),
        color=0x5865F2
    )
    embed.set_footer(text=f"員工編號：{empid} · 查詢時間：{datetime.now().strftime('%H:%M')}")
    await interaction.followup.send(embed=embed, ephemeral=True)
async def today_punch_time_removed():
    pass  # 已合併到今日狀態

async def duty_list_placeholder(interaction: discord.Interaction):
    pass  # 已合併到值班與休假日程查詢

@tree.command(name="查詢值班休假日程", description="查看所有值班和休假日期")
async def duty_leave_list(interaction: discord.Interaction):
    user_data = get_user_data(interaction.user.id)
    today = date.today()
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]

    duties = sorted(user_data.get("duty_days", []))
    leaves = sorted(user_data.get("leave_dates", []))

    today_str = today.strftime("%Y-%m-%d")
    duty_past = [d for d in duties if d < today_str]
    leave_past = [d for d in leaves if d < today_str]

    lines = []
    lines.extend(format_future_month_schedule_sections(user_data, today, max_extra_months=None))

    if duty_past or leave_past:
        past_lines = ["**📋 過去紀錄（最近5筆）**"]
        all_past = sorted(
            [(d, "🌙值班") for d in duty_past] +
            [(d, "🏖️請假") for d in leave_past]
        )
        for d, label in all_past[-5:]:
            dt = date.fromisoformat(d)
            wd = weekday_names[dt.weekday()]
            past_lines.append(f"{label} {dt.month}/{dt.day}（{wd}）")
        if len(all_past) > 5:
            past_lines.append(f"...等共 {len(all_past)} 筆")
        lines.append("\n".join(past_lines))

    embed = discord.Embed(
        title="📅 值班與休假日程",
        description="\n\n".join(lines),
        color=0x9b59b6
    )
    embed.set_footer(text="使用 /下月設定 可完成下月設定，或用 /值班休假設定 追加")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── 查詢打卡記錄 ──
@tree.command(name="查詢本日e-hr刷卡記錄", description="查詢今天的 e-HR 刷卡記錄（未綁定者請填入員工編號和密碼）")
@app_commands.describe(
    員工編號="未綁定帳號時填入（已綁定者可不填）",
    密碼="未綁定帳號時填入（已綁定者可不填）"
)
async def query_punch_record(interaction: discord.Interaction, 員工編號: str = None, 密碼: str = None):
    # 立刻 defer，避免 3 秒逾時
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except discord.errors.HTTPException as e:
        if e.code == 40060:
            # 已經被 acknowledge，繼續用 followup 回應
            pass
        else:
            return
    except Exception as e:
        return

    user_data = get_user_data(interaction.user.id)
    # 優先使用指令傳入的帳號密碼，其次用綁定的帳號
    empid = 員工編號 or user_data.get("empid")
    password = 密碼 or user_data.get("password")

    # 未綁定時，提示輸入帳號密碼
    if not empid or not password:
        embed = discord.Embed(
            title="❌ 需要帳號密碼",
            description=(
                "請使用以下方式之一查詢：\n\n"
                "1️⃣ **已綁定帳號**：直接輸入 `/查詢本日e-hr刷卡記錄` 即可\n"
                "2️⃣ **未綁定帳號**：輸入 `/查詢本日e-hr刷卡記錄 員工編號:12345 密碼:yourpass`"
            ),
            color=0xffaa00
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    try:
        loop = asyncio.get_event_loop()

        def do_query():
            return _query_today_from_monthly_b9(empid, password)

        result = await loop.run_in_executor(None, do_query)

        today = date.today()
        today_str = today.strftime("%Y-%m-%d")
        roc_year = today.year - 1911
        date_label = f"{roc_year}/{today.month:02d}/{today.day:02d}"
        now_time = datetime.now().strftime("%H:%M")

        uid_str = str(interaction.user.id)
        user_schedule = scheduled_times.get(uid_str, {})
        user_data_for_schedule = get_user_data(interaction.user.id)
        yesterday = today - timedelta(days=1)
        is_duty_after = is_duty_day(user_data_for_schedule, yesterday)

        # punched_today（③線）
        punched_now = load_punched_today()
        dutyout_key = f"{uid_str}-dutyout-{today_str}"
        out_key     = f"{uid_str}-out-{today_str}"
        punched_duty_t = punched_now.get(dutyout_key) if dutyout_key in punched_now else None
        punched_out_t  = punched_now.get(out_key)     if out_key     in punched_now else None

        has_data = result.get("success") and (result.get("times") or result.get("clock_in") or result.get("clock_out"))

        if has_data:
            # ── 四線推算 ──
            inferred = infer_punch_times(result, is_duty_after)
            eff_in     = inferred["inferred_in"]
            eff_out    = inferred["inferred_out"]
            in_source  = inferred["in_source"]
            out_source = inferred["out_source"]
            times_set = set(result.get("raw_times", [])) | set(result.get("makeup_times", []))
            if result.get("next_day_out"):
                times_set.add(result["next_day_out"])
            times_list = sorted(times_set) or result.get("times", [])

            # ── 刷卡列表 ──
            desc_lines = [f"**{date_label} 刷卡記錄**", ""]
            if times_list:
                for t in times_list:
                    desc_lines.append(f"\t🟢 {t}")
                # 值班隔天：標注 < 08:00 的誤刷
                if is_duty_after:
                    bad = [t for t in times_list if _t2m(t) < 8 * 60]
                    if bad:
                        desc_lines.append(f"\t（{'、'.join(bad)} 為值班期間誤刷，忽略）")
            else:
                desc_lines.append("\t（無詳細刷卡記錄）")
            desc_lines.append("")

            # ── 上班欄 ──
            if is_duty_after:
                desc_lines.append("🌙 值班隔天，不顯示上班卡")
            elif is_duty_day(user_data_for_schedule, today):
                # 值班當天：顯示上班時間
                if eff_in and in_source == "ehr":
                    desc_lines.append(f"**上班：** {eff_in}（{fmt_source(in_source, is_in=True)}）")
                elif eff_in and in_source == "times":
                    desc_lines.append(f"**上班：** {eff_in}（e-HR 有記錄但刷卡/出卡時間待定）")
            else:
                # 平日
                if eff_in and in_source == "ehr":
                    desc_lines.append(f"**上班：** {eff_in}（{fmt_source(in_source, is_in=True)}）")
                elif eff_in and in_source == "times":
                    desc_lines.append(f"**上班：** {eff_in}（e-HR 有記錄但刷卡/出卡時間待定）")

            # ── 下班欄 ──
            if is_duty_day(user_data_for_schedule, today):
                desc_lines.append("**下班：** 值班日，不打下班卡")
                desc_lines.append(f"　↳ 明天 **{user_schedule.get('dutyout', '08:05~08:40')}** 自動打值班下班卡")
            elif is_duty_after:
                if eff_out and out_source in ("ehr", "ehr_next_day"):
                    desc_lines.append(f"**值班下班：** {eff_out}（{fmt_source(out_source, is_in=False)}）")
                elif eff_out and out_source == "times":
                    desc_lines.append(f"**值班下班：** {eff_out}（e-HR 有記錄但刷卡/出卡時間待定）")
                elif punched_duty_t is not None:
                    t = punched_duty_t if punched_duty_t else user_schedule.get('dutyout', '')
                    desc_lines.append(f"**值班下班：** ✅ 已在 {t} 打卡\n　（此為自動打卡預設時間，e-HR 尚未記錄）")
                else:
                    dutyout_time = user_schedule.get('dutyout', '')
                    desc_lines.append("**值班下班：** 尚未刷卡")
                    if dutyout_time:
                        desc_lines.append(f"　↳ 預計 {dutyout_time} 自動打值班下班卡")
            else:
                if eff_out and out_source in ("ehr", "ehr_next_day"):
                    desc_lines.append(f"**下班：** {eff_out}（{fmt_source(out_source, is_in=False)}）")
                elif eff_out and out_source == "times":
                    desc_lines.append(f"**下班：** {eff_out}（e-HR 有記錄但刷卡/出卡時間待定）")
                elif punched_out_t is not None:
                    t = punched_out_t if punched_out_t else user_schedule.get('out', '')
                    desc_lines.append(f"**下班：** ✅ 已在 {t} 打卡\n　（此為自動打卡預設時間，e-HR 尚未記錄）")
                else:
                    out_time = user_schedule.get('out', '')
                    desc_lines.append("**下班：** 尚未刷卡")
                    if out_time:
                        desc_lines.append(f"　↳ 預計 {out_time} 自動打下班卡")

            embed = discord.Embed(
                title="📋 e-HR 打卡記錄查詢",
                description="\n".join(desc_lines),
                color=0x00ff00
            )
        else:
            # ── 查無刷卡記錄（e-HR 完全未更新）──
            desc_lines = [f"**{date_label}**", "", "查無刷卡記錄（e-HR 尚未更新）", ""]

            if is_duty_day(user_data_for_schedule, today):
                in_time = user_schedule.get("in", "")
                desc_lines.append(f"**預計上班打卡：** {in_time}" if in_time else "**預計上班打卡：** 排程未取得")
                desc_lines.append(f"**下班：** 值班日，不打下班卡，明天 **{user_schedule.get('dutyout', '08:05~08:40')}** 自動打值班下班卡")
            elif is_duty_after:
                desc_lines.append("🌙 值班隔天，不顯示上班卡")
                if punched_duty_t is not None:
                    t = punched_duty_t if punched_duty_t else user_schedule.get('dutyout', '')
                    desc_lines.append(f"**值班下班：** ✅ 已在 {t} 打卡\n　（此為自動打卡預設時間，e-HR 尚未記錄）")
                else:
                    dutyout_time = user_schedule.get('dutyout', '')
                    desc_lines.append(f"**預計值班下班打卡：** {dutyout_time}" if dutyout_time else "**預計值班下班：** 排程未取得")
            else:
                in_time = user_schedule.get("in", "")
                out_time = user_schedule.get("out", "")
                desc_lines.append(f"**預計上班打卡：** {in_time}" if in_time else "**預計上班打卡：** 排程未取得")
                if punched_out_t is not None:
                    t = punched_out_t if punched_out_t else out_time
                    desc_lines.append(f"**下班：** ✅ 已在 {t} 打卡\n　（此為自動打卡預設時間，e-HR 尚未記錄）")
                else:
                    desc_lines.append(f"**預計下班打卡：** {out_time}" if out_time else "**預計下班打卡：** 排程未取得")

            embed = discord.Embed(
                title="📋 e-HR 打卡記錄查詢",
                description="\n".join(desc_lines),
                color=0xffaa00
            )

        embed.set_footer(text=f"員工編號：{empid} · 查詢時間：{now_time}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        embed = discord.Embed(
            title="❌ 查詢失敗",
            description=str(e),
            color=0xff0000
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

# ── 本月人資系統打卡紀錄（手動查詢版）──
@tree.command(name="查詢本月e-hr刷出卡紀錄", description="查詢本月 e-HR 紀錄的上下班時間，未綁定者可輸入員工編號和密碼查詢")
@app_commands.describe(
    員工編號="未綁定帳號時填入（已綁定者可不填）",
    密碼="未綁定帳號時填入（已綁定者可不填）"
)
async def monthly_summary_command(interaction: discord.Interaction, 員工編號: str = None, 密碼: str = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    user_data = get_user_data(interaction.user.id)
    empid = 員工編號 or user_data.get("empid")
    password = 密碼 or user_data.get("password")
    if not empid or not password:
        embed = discord.Embed(
            title="❌ 需要帳號密碼",
            description=(
                "請使用以下方式之一查詢：\n\n"
                "1️⃣ **已綁定帳號**：直接輸入 `/查詢本月e-hr刷出卡紀錄` 即可\n"
                "2️⃣ **未綁定帳號**：輸入 `/查詢本月e-hr刷出卡紀錄 員工編號:12345 密碼:yourpass`"
            ),
            color=0xffaa00
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    loop = asyncio.get_event_loop()
    summary_user_data = user_data if not 員工編號 and not 密碼 else None
    summary_text = await loop.run_in_executor(None, lambda: _query_monthly_summary(empid, password, summary_user_data))
    # 分頁處理（超過 3800 字元時分割）
    if len(summary_text) > 3800:
        half = len(summary_text) // 2
        embed1 = discord.Embed(title="📊 本月上下班紀錄（上）", description=summary_text[:half], color=0x5865F2)
        embed2 = discord.Embed(title="📊 本月上下班紀錄（下）", description=summary_text[half:], color=0x5865F2)
        await interaction.followup.send(embeds=[embed1, embed2], ephemeral=True)
    else:
        embed = discord.Embed(title="📊 本月上下班紀錄", description=summary_text, color=0x5865F2)
        embed.set_footer(text=f"查詢時間：{datetime.now().strftime('%H:%M')}")
        await interaction.followup.send(embed=embed, ephemeral=True)

# ── 管理員：查看所有綁定使用者狀態 ──
ADMIN_IDS = {645516761189318707}  # 填入管理員的 Discord User ID


class AdminBindModal(discord.ui.Modal, title='管理員代綁定帳號'):
    empid = discord.ui.TextInput(label='員工編號', placeholder='請輸入員工編號', required=True)
    password = discord.ui.TextInput(label='密碼', placeholder='請輸入密碼', required=True, style=discord.TextStyle.short)

    def __init__(self, target_user, admin_user_id):
        super().__init__()
        self.target_user = target_user
        self.admin_user_id = admin_user_id

    async def on_submit(self, interaction: discord.Interaction):
        if await reject_non_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            empid = self.empid.value.strip()
            password = self.password.value.strip()
            validation = await asyncio.get_event_loop().run_in_executor(
                None, lambda: validate_ehr_login(empid, password)
            )
            if not validation.get("success"):
                embed = discord.Embed(
                    title="❌ 代綁定失敗",
                    description=f"{validation.get('message')}\n\n未變更 {self.target_user.mention} 的帳號資料。",
                    color=0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            user_data = get_user_data(self.target_user.id)
            user_data["empid"] = empid
            user_data["password"] = password
            enable_auto_punch(user_data)
            mark_rebind_confirm_only(user_data)
            save_user_data(self.target_user.id, user_data)

            asyncio.create_task(new_user_punch_assessment(
                discord_user=self.target_user,
                uid_str=str(self.target_user.id),
                empid=empid,
                password=password,
                user_data=user_data,
                admin_initiated_by=self.admin_user_id,
            ))

            embed = discord.Embed(
                title="✅ 代綁定成功",
                description=(
                    f"對象：{self.target_user.mention}\n"
                    f"員工編號：**{empid}**\n\n"
                    "已開啟自動打卡，並已啟動今日打卡評估。\n"
                    "本次代綁定前已過的排程不會自動補打；如需補打，會請使用者按鈕確認。"
                ),
                color=0x00ff00
            )
        except Exception as e:
            embed = discord.Embed(title="❌ 錯誤", description=str(e), color=0xff0000)
        await interaction.followup.send(embed=embed, ephemeral=True)

@admin_group.command(name="綁定", description="管理員代替使用者綁定 e-HR 帳號（需帳密）")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(使用者="要代為綁定的 Discord 使用者")
async def admin_bind_user(interaction: discord.Interaction, 使用者: discord.User):
    if await reject_non_admin(interaction):
        return
    await interaction.response.send_modal(AdminBindModal(使用者, interaction.user.id))

@admin_group.command(name="解除綁定", description="管理員代替使用者解除 e-HR 帳號綁定")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(使用者="要解除綁定的 Discord 使用者")
async def admin_unbind_user(interaction: discord.Interaction, 使用者: discord.User):
    if await reject_non_admin(interaction):
        return
    user_data = get_user_data(使用者.id)
    user_data["empid"] = None
    user_data["password"] = None
    user_data["auto_punch"] = False
    save_user_data(使用者.id, user_data)
    embed = discord.Embed(
        title="✅ 已解除綁定",
        description=f"對象：{使用者.mention}\n帳號已解除，自動打卡停止。",
        color=0xffaa00
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@admin_group.command(name="恢復自動打卡", description="管理員替指定使用者恢復今天的自動打卡")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(使用者="要恢復自動打卡的 Discord 使用者")
async def admin_resume_auto(interaction: discord.Interaction, 使用者: discord.User):
    if await reject_non_admin(interaction):
        return
    user_data = get_user_data(使用者.id)
    if not user_data.get("empid") or not user_data.get("password"):
        embed = discord.Embed(
            title="❌ 尚未綁定帳號",
            description=f"對象：{使用者.mention}\n請先使用 `/管理 綁定` 完成帳號綁定。",
            color=0xff0000,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    cancelled_today = enable_auto_punch(user_data)
    save_user_data(使用者.id, user_data)
    detail = "已移除今日取消設定，今天恢復自動打卡。" if cancelled_today else "今天的自動打卡已是開啟狀態。"
    embed = discord.Embed(
        title="▶️ 已恢復自動打卡",
        description=f"對象：{使用者.mention}\n{detail}",
        color=0x00ff00,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def admin_update_dates(interaction, target_user, raw_dates, kind, mode, month_value=None):
    if await reject_non_admin(interaction):
        return
    field = "duty_days" if kind == "duty" else "leave_dates"
    user_data = get_user_data(target_user.id)
    source = f"admin_{kind}_setting"
    added, removed, failed = update_date_setting(user_data, field, raw_dates, mode, month_value, source)
    save_user_data(target_user.id, user_data)
    embed = build_date_setting_embed(kind, mode, added, removed, failed, target_user=target_user)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@admin_group.command(name="值班休假設定", description="管理員替指定使用者設定、新增或取消值班/休假日期")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    使用者="要設定的 Discord 使用者",
    類型="選擇要調整值班或休假",
    動作="選擇設定、新增或取消",
    月份="要調整的月份，省略則使用本月",
    日期="輸入日期，例如：1 3 5 或 7/1 7/3",
)
@app_commands.choices(類型=SCHEDULE_KIND_CHOICES, 動作=SCHEDULE_ACTION_CHOICES)
async def admin_duty_leave_setting(
    interaction: discord.Interaction,
    使用者: discord.User,
    類型: str,
    動作: str,
    日期: str,
    月份: int = None,
):
    await admin_update_dates(interaction, 使用者, 日期, 類型, 動作, 月份)

async def send_admin_next_month_settings_panel(interaction: discord.Interaction, 使用者: discord.User, source="admin_next_month_settings"):
    if await reject_non_admin(interaction):
        return
    user_data = get_user_data(使用者.id)
    target_month = month_key_for_next_month()
    embed = discord.Embed(
        title="📌 下月設定",
        description=(
            f"對象：{使用者.mention}\n"
            f"請選擇 **{target_month}** 的下月設定方式。\n\n"
            "如果下個月沒有值班也沒有休假，請按「是」。\n"
            "如果要填值班或休假日期，請按「否」。\n\n"
            f"{format_month_schedule_summary(user_data, target_month)}"
        ),
        color=0x5865F2,
    )
    await interaction.response.send_message(
        embed=embed,
        view=NextMonthReminderView(
            target_uid=使用者.id,
            actor_uid=interaction.user.id,
            target_label=使用者.mention,
            source=source,
            require_binding=False,
        ),
        ephemeral=True,
    )

@admin_group.command(name="下月設定", description="管理員替指定使用者設定下個月自動打卡與休假值班日")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(使用者="要設定的 Discord 使用者")
async def admin_next_month_settings(interaction: discord.Interaction, 使用者: discord.User):
    await send_admin_next_month_settings_panel(interaction, 使用者, source="admin_next_month_settings")

@admin_group.command(name="帳號", description="查看所有綁定使用者的狀態")
@app_commands.default_permissions(administrator=True)
async def admin_query(interaction: discord.Interaction):
    if await reject_non_admin(interaction):
        return
    data = load_data()
    if not data:
        embed = discord.Embed(title="📋 使用者狀態", description="目前無任何綁定使用者", color=0xffaa00)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    lines = []
    today = date.today()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    now_hm = datetime.now().strftime("%H:%M")
    punched_now = load_punched_today()
    saved_schedules = load_schedule_today()

    bound_users = [(uid, ud) for uid, ud in data.items() if ud.get("empid")]
    loop = asyncio.get_event_loop()
    query_tasks = [
        loop.run_in_executor(
            None,
            _query_today_from_monthly_b9,
            str(ud.get("empid")),
            ud.get("password"),
            today,
        )
        for uid, ud in bound_users
    ]
    query_values = await asyncio.gather(*query_tasks, return_exceptions=True)
    ehr_results = {
        uid: (
            value
            if isinstance(value, dict)
            else {"success": False, "message": f"查詢失敗：{value}"}
        )
        for (uid, _), value in zip(bound_users, query_values)
    }

    def scheduled(uid, kind):
        user_schedule = saved_schedules.get(uid) or scheduled_times.get(uid, {})
        return user_schedule.get(kind, "")

    def punched_time(uid, kind):
        return punched_now.get(f"{uid}-{kind}-{today_str}")

    def done_or_wait(uid, kind, label, schedule_time, actual_time=None, actual_source=None):
        if actual_time:
            if actual_source == "ehr":
                source_label = "e-HR 判定"
            elif actual_source == "ehr_next_day":
                source_label = "e-HR 判定（隔天）"
            else:
                source_label = "e-HR 原始／補刷卡"
            return f"✅ {label}：{actual_time}（{source_label}）"
        punched_at = punched_time(uid, kind)
        if punched_at is not None:
            bot_time = punched_at or schedule_time or "已執行"
            return f"⚠️ {label}：Bot {bot_time} 已執行，e-HR 尚無紀錄"
        if schedule_time:
            if schedule_time <= now_hm:
                return f"⚠️ {label}：預計 {schedule_time}，尚無成功紀錄"
            return f"⏳ {label}：等待中（預計 {schedule_time}）"
        return f"⏳ {label}：尚未成功"

    for uid, ud in data.items():
        empid = ud.get("empid")
        if not empid:
            continue
        auto = "✅ 開啟" if ud.get("auto_punch", True) else "❌ 關閉"

        is_duty_today = is_duty_day(ud, today)
        is_duty_after = is_duty_day(ud, yesterday)
        is_leave_today = is_leave_day(ud, today)
        is_weekend_today = is_weekend(today)
        is_cancelled_today = is_auto_cancelled(ud, today)

        scheduled_in = scheduled(uid, "in")
        scheduled_out = scheduled(uid, "out")
        scheduled_duty = scheduled(uid, "dutyout")
        ehr_result = ehr_results.get(uid, {"success": False, "message": "查詢失敗"})
        inferred = infer_punch_times(ehr_result, is_duty_after) if ehr_result.get("success") else {
            "inferred_in": None, "inferred_out": None, "in_source": None, "out_source": None
        }
        actual_in = inferred.get("inferred_in")
        actual_out = inferred.get("inferred_out")

        today_lines = []
        if is_cancelled_today:
            mode = "⏸️ 今日已取消自動打卡"
            today_lines.append("　今日打卡：手動模式")
        elif is_duty_after:
            mode = "🌙 昨日值班後"
            if not ud.get("auto_punch", True) and auto_disabled_by_monthly(ud, today):
                today_lines.append("　本月續用未確認：只保留值班下班卡，不打一般上下班卡")
            today_lines.append("　上班：⏭️ 值班隔天不打上班卡")
            today_lines.append("　" + done_or_wait(uid, "dutyout", "值班下班", scheduled_duty, actual_out, inferred.get("out_source")))
        elif not ud.get("auto_punch", True):
            mode = "❌ 自動打卡關閉"
            if auto_disabled_by_monthly(ud, today):
                today_lines.append("　今日打卡：本月續用未確認，不會自動執行")
            else:
                today_lines.append("　今日打卡：不會自動執行")
        elif is_leave_today:
            mode = "🏖️ 今日休假"
            today_lines.append("　上班：⏭️ 休假不打卡")
            today_lines.append("　下班：⏭️ 休假不打卡")
        elif is_weekend_today and not is_duty_today:
            mode = "📴 週末"
            today_lines.append("　上班：⏭️ 週末不打卡")
            today_lines.append("　下班：⏭️ 週末不打卡")
        elif is_duty_today:
            mode = "🌙 今日值班"
            today_lines.append("　" + done_or_wait(uid, "in", "上班", scheduled_in, actual_in, inferred.get("in_source")))
            today_lines.append("　下班：⏭️ 今日值班不打下班卡")
            today_lines.append(f"　值班下班：明天 {scheduled_duty or '08:05~08:40'}")
        else:
            mode = "🟢 平日"
            today_lines.append("　" + done_or_wait(uid, "in", "上班", scheduled_in, actual_in, inferred.get("in_source")))
            today_lines.append("　" + done_or_wait(uid, "out", "下班", scheduled_out, actual_out, inferred.get("out_source")))

        schedule_sections = format_future_month_schedule_sections(
            ud,
            today,
            include_current=True,
            include_next=True,
            max_extra_months=3,
        )
        schedule_detail = "\n".join("　" + line.replace("\n", "\n　") for line in schedule_sections)
        lines.append(
            f"👤 <@{uid}>　員工編號：{empid}　自動打卡：{auto}\n"
            f"　今日模式：{mode}\n"
            + "\n".join(today_lines) + "\n"
            f"{schedule_detail}"
        )
    embed = discord.Embed(
        title=f"📋 所有綁定使用者（共 {len(lines)} 人）｜{today_str}",
        description="\n\n".join(lines) if lines else "無綁定使用者",
        color=0x5865F2
    )
    embed.set_footer(text=f"查詢時間：{datetime.now().strftime('%H:%M')}")
    await interaction.followup.send(embed=embed, ephemeral=True)

@admin_group.command(name="今日打卡驗證", description="管理員查詢所有綁定使用者今日 e-HR 實際刷卡紀錄")
@app_commands.default_permissions(administrator=True)
async def admin_verify_today(interaction: discord.Interaction):
    if await reject_non_admin(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    data = load_data()
    bound_users = [(uid, ud) for uid, ud in data.items() if ud.get("empid")]
    if not bound_users:
        embed = discord.Embed(title="📋 今日 e-HR 打卡驗證", description="目前無任何綁定使用者", color=0xffaa00)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    today = date.today()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    now_hm = datetime.now().strftime("%H:%M")
    saved_schedules = load_schedule_today()
    punched_now = load_punched_today()

    def ehr_query_one(empid, password):
        if not password:
            return {"success": False, "message": "未儲存密碼，無法登入 e-HR"}
        try:
            return _query_today_from_monthly_b9(empid, password, today)
        except Exception as e:
            return {"success": False, "message": f"查詢失敗：{str(e)}"}

    def verify_all_users():
        results = {}
        for uid, ud in bound_users:
            results[uid] = ehr_query_one(ud.get("empid"), ud.get("password"))
        return results

    loop = asyncio.get_event_loop()
    ehr_results = await loop.run_in_executor(None, verify_all_users)

    def source_text(source):
        if source == "ehr":
            return "e-HR 判定"
        if source == "ehr_next_day":
            return "e-HR 判定（隔天）"
        if source == "times":
            return "e-HR 原始刷卡"
        return "e-HR"

    def actual_line(label, actual_t, source):
        if actual_t:
            return f"✅ {label}：{actual_t}（{source_text(source)}）"
        return f"⚠️ {label}：e-HR 尚無紀錄"

    def user_schedule(uid):
        return saved_schedules.get(uid) or scheduled_times.get(uid, {})

    def comparison_line(uid, kind, label, scheduled_t, actual_t, ehr_available):
        if not ehr_available:
            return f"　⚠️ {label}比對：e-HR 查詢失敗，無法比對"
        if not scheduled_t:
            return f"　⚠️ {label}比對：Bot 排程未取得"
        if actual_t:
            if scheduled_t == actual_t:
                return f"　✅ {label}比對：Bot {scheduled_t} ＝ e-HR {actual_t}，相符"
            return (
                f"　⚠️ {label}比對：Bot {scheduled_t} ≠ e-HR {actual_t}，不相符\n"
                "　　e-HR 已有刷卡紀錄，不代表打卡失敗"
            )

        punched_at = punched_now.get(f"{uid}-{kind}-{today_str}")
        if punched_at is not None:
            bot_t = punched_at or scheduled_t
            return f"　⚠️ {label}比對：Bot {bot_t} 已執行，e-HR 尚無紀錄"
        if scheduled_t <= now_hm:
            return f"　⚠️ {label}比對：Bot 排程 {scheduled_t} 已過，e-HR 尚無紀錄"
        return f"　⏳ {label}比對：尚未到排程時間 {scheduled_t}"

    lines = []
    for uid, ud in bound_users:
        empid = ud.get("empid")
        result = ehr_results.get(uid, {"success": False, "message": "查詢失敗"})
        is_duty_today = is_duty_day(ud, today)
        is_duty_after = is_duty_day(ud, yesterday)
        is_leave_today = is_leave_day(ud, today)
        is_weekend_today = is_weekend(today)
        is_cancelled_today = is_auto_cancelled(ud, today)

        if not ud.get("auto_punch", True):
            mode = "❌ 自動打卡關閉"
        elif is_cancelled_today:
            mode = "⏸️ 今日已取消自動打卡"
        elif is_duty_after:
            mode = "🌙 昨日值班後"
        elif is_leave_today:
            mode = "🏖️ 今日休假"
        elif is_weekend_today and not is_duty_today:
            mode = "📴 週末"
        elif is_duty_today:
            mode = "🌙 今日值班"
        else:
            mode = "🟢 平日"
        no_auto_expected = (
            not ud.get("auto_punch", True)
            or is_cancelled_today
            or (
                (is_leave_today or (is_weekend_today and not is_duty_today))
                and not expects_dutyout_check(ud, today)
            )
        )
        schedule = user_schedule(uid)
        scheduled_in = schedule.get("in", "")
        scheduled_out = schedule.get("out", "")
        scheduled_duty = schedule.get("dutyout", "")
        actual_in = None
        actual_out = None
        result_message = result.get("message", "")
        ehr_available = result.get("success", False) or "今日尚無刷卡記錄" in result_message

        detail_lines = [f"👤 <@{uid}>　員工編號：{empid}", f"　今日模式：{mode}"]
        if result.get("success"):
            inferred = infer_punch_times(result, is_duty_after)
            actual_in = inferred.get("inferred_in")
            actual_out = inferred.get("inferred_out")
            raw_values = result.get("raw_times", [])
            makeup_values = result.get("makeup_times", [])
            raw_times = "、".join(raw_values) if raw_values else "無"
            makeup_times = "、".join(makeup_values) if makeup_values else "無"

            if is_duty_after:
                detail_lines.append("　上班：⏭️ 值班隔天不打上班卡")
                detail_lines.append(
                    "　" + actual_line("值班下班", inferred.get("inferred_out"), inferred.get("out_source"))
                )
            elif is_leave_today:
                detail_lines.append("　上班：⏭️ 休假不打卡")
                detail_lines.append("　下班：⏭️ 休假不打卡")
            elif is_weekend_today and not is_duty_today:
                detail_lines.append("　上班：⏭️ 週末不打卡")
                detail_lines.append("　下班：⏭️ 週末不打卡")
            elif is_duty_today:
                detail_lines.append(
                    "　" + actual_line("上班", inferred.get("inferred_in"), inferred.get("in_source"))
                )
                detail_lines.append("　下班：⏭️ 今日值班不打下班卡")
                detail_lines.append("　值班下班：明天才驗證")
            else:
                detail_lines.append(
                    "　" + actual_line("上班", inferred.get("inferred_in"), inferred.get("in_source"))
                )
                detail_lines.append(
                    "　" + actual_line("下班", inferred.get("inferred_out"), inferred.get("out_source"))
                )

            detail_lines.append(f"　e-HR 原始刷卡：{raw_times}")
            if makeup_values:
                detail_lines.append(f"　e-HR 補刷卡：{makeup_times}")
        else:
            message = result.get("message", "今日尚無刷卡記錄")
            if no_auto_expected and "今日尚無刷卡記錄" in message:
                detail_lines.append("　✅ e-HR 今日無刷卡紀錄（符合今日模式）")
            else:
                detail_lines.append(f"　⚠️ e-HR 查詢結果：{message}")

        detail_lines.append("")
        if no_auto_expected:
            detail_lines.append("　🤖 Bot 排程：今日不自動打卡")
        elif is_duty_after:
            duty_text = scheduled_duty or "排程未取得"
            detail_lines.append(f"　🤖 Bot 排程：值班下班 {duty_text}")
            detail_lines.append(
                comparison_line(uid, "dutyout", "值班下班", scheduled_duty, actual_out, ehr_available)
            )
        elif is_duty_today:
            in_text = scheduled_in or "排程未取得"
            duty_text = scheduled_duty or "排程未取得"
            detail_lines.append(f"　🤖 Bot 排程：上班 {in_text}｜值班下班（明天）{duty_text}")
            detail_lines.append(comparison_line(uid, "in", "上班", scheduled_in, actual_in, ehr_available))
            detail_lines.append("　⏭️ 值班下班比對：明天才驗證")
        else:
            in_text = scheduled_in or "排程未取得"
            out_text = scheduled_out or "排程未取得"
            detail_lines.append(f"　🤖 Bot 排程：上班 {in_text}｜下班 {out_text}")
            detail_lines.append(comparison_line(uid, "in", "上班", scheduled_in, actual_in, ehr_available))
            detail_lines.append(comparison_line(uid, "out", "下班", scheduled_out, actual_out, ehr_available))

        lines.append("\n".join(detail_lines))

    embeds = []
    current = []
    current_len = 0
    for block in lines:
        block_len = len(block) + 2
        if current and current_len + block_len > 3800:
            embeds.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(block)
        current_len += block_len
    if current:
        embeds.append("\n\n".join(current))

    for idx, desc in enumerate(embeds, start=1):
        suffix = f"（{idx}/{len(embeds)}）" if len(embeds) > 1 else ""
        embed = discord.Embed(
            title=f"📋 今日 e-HR 打卡驗證｜{today_str}{suffix}",
            description=desc,
            color=0x5865F2
        )
        embed.set_footer(text=f"查詢時間：{datetime.now().strftime('%H:%M')} · 來源：e-HR 即時查詢")
        await interaction.followup.send(embed=embed, ephemeral=True)

# ── 通知設定 ──
notify_type_choices = [
    app_commands.Choice(name="早晨提醒（每天06:50提醒今日打卡排程）", value="morning"),
    app_commands.Choice(name="打卡前提醒（打卡前10分鐘提醒）", value="pre_punch"),
    app_commands.Choice(name="下班比對（平日18:00和值班隔天09:00比對）", value="compare"),
    app_commands.Choice(name="月底摘要（每月最後一天19:00發送）", value="monthly"),
]

@tree.command(name="通知設定", description="個別開關各種自動通知")
@app_commands.describe(類型="選擇要設定的通知類型", 開關="開啟或關閉")
@app_commands.choices(
    類型=notify_type_choices,
    開關=[
        app_commands.Choice(name="開啟", value="on"),
        app_commands.Choice(name="關閉", value="off"),
    ]
)
async def notify_setting(interaction: discord.Interaction, 類型: str, 開關: str):
    user_data = get_user_data(interaction.user.id)
    if "notify" not in user_data:
        user_data["notify"] = {"morning": True, "pre_punch": True, "compare": True, "monthly": True}
    user_data["notify"][類型] = (開關 == "on")
    save_user_data(interaction.user.id, user_data)

    type_names = {
        "morning": "早晨提醒（06:50）",
        "pre_punch": "打卡前提醒",
        "compare": "下班比對（18:00/09:00）",
        "monthly": "月底摘要",
    }
    status = "✅ 已開啟" if 開關 == "on" else "❌ 已關閉"
    embed = discord.Embed(
        title="🔔 通知設定已更新",
        description=f"{type_names[類型]}：{status}",
        color=0x00ff00 if 開關 == "on" else 0xff0000
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="通知全關", description="一鍵關閉所有自動通知（打卡成功/失敗通知不受影響）")
async def notify_all_off(interaction: discord.Interaction):
    user_data = get_user_data(interaction.user.id)
    user_data["notify"] = {"morning": False, "pre_punch": False, "compare": False, "monthly": False}
    save_user_data(interaction.user.id, user_data)
    embed = discord.Embed(
        title="🔕 已關閉所有通知",
        description=(
            "以下通知已全部關閉：\n"
            "❌ 早晨提醒（06:50）\n"
            "❌ 打卡前提醒\n"
            "❌ 下班比對（18:00/09:00）\n"
            "❌ 月底摘要\n\n"
            "打卡成功/失敗通知不受影響，仍會正常發送。\n"
            "使用 `/通知設定` 可個別重新開啟。"
        ),
        color=0xff0000
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── 說明 ──
@tree.command(name="說明", description="查看所有指令的使用說明")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 打卡機器人使用說明",
        description="這個機器人可以幫你在院外進行 e-HR 打卡。你可以選擇每次手動打卡，或綁定帳號後使用自動打卡功能。所有訊息只有自己看得到。",
        color=0x5865F2
    )
    embed.add_field(
        name="🖐️ 手動打卡（不需綁定帳號）",
        value="`/打卡` — 選擇上班或下班，輸入員工編號和密碼即可打卡。適合偶爾需要打卡、或不想設定自動打卡的人使用。",
        inline=False
    )
    embed.add_field(
        name="🤖 自動打卡說明",
        value=(
            "若需要自動打卡，請先使用 `/帳號綁定` 綁定員工編號和密碼。"
            "綁定後系統會依照設定在指定時間範圍內自動打卡。\n"
            "**⏰ 打卡時間範圍：**\n"
            "平日上班：07:00~07:40\n"
            "平日下班：17:05~17:40\n"
            "值班當天：只打上班卡，不打下班卡\n"
            "值班下班（隔天08:05~08:40打）：只打值班下班卡，不打上班卡\n"
            "週六日：不自動打卡（值班日除外）"
        ),
        inline=False
    )
    embed.add_field(
        name="👤 帳號設定",
        value="`/帳號綁定` — 綁定員工編號+密碼，開啟自動打卡\n　　　　　　綁定後自動評估當天打卡狀況：\n　　　　　　• 排程時間已過且 e-HR 無記錄 → 私訊補打按鈕\n　　　　　　• 超過 08:00 未打上班卡 → 私訊告知（不補打，避免遲到）\n　　　　　　• 排程時間未到 → 等待自動打卡，不另通知\n`/下月設定` — 設定下個月繼續自動打卡、值班與休假\n`/帳號解除` — 取消綁定，停止自動打卡",
        inline=False
    )
    if is_admin_interaction(interaction):
        embed.add_field(
            name="🛠️ 管理員指令",
            value=(
                "`/管理 帳號` — 查看所有綁定使用者狀態、本月/下月/後續月份值班休假\n"
                "`/管理 今日打卡驗證` — 查詢所有綁定使用者今日 e-HR 實際刷卡紀錄\n"
                "`/管理 綁定`、`/管理 解除綁定` — 代替指定使用者管理帳號\n"
                "`/管理 恢復自動打卡` — 替指定使用者移除今日取消設定\n"
                "`/管理 下月設定` — 替指定使用者設定下個月繼續自動打卡、值班與休假\n"
                "`/管理 值班休假設定` — 代替指定使用者調整值班/休假日期"
            ),
            inline=False
        )
    embed.add_field(
        name="📌 下月設定",
        value=(
            "`/下月設定` — 顯示「是/否」按鈕選擇下月設定方式\n"
            "選「是，下月無值班無休假」會清空下月值班/休假並繼續自動打卡\n"
            "選「否，我要填值班/休假」會開表單填下月日期。"
        ),
        inline=False
    )
    embed.add_field(
        name="📅 值班/休假設定",
        value=(
            "`/值班休假設定 類型:值班 動作:設定 月份:7 日期:1 3 5` — **重設**指定月份值班\n"
            "`/值班休假設定 類型:值班 動作:新增 月份:7 日期:6 8` — **追加**值班日，不影響同月現有設定\n"
            "`/值班休假設定 類型:值班 動作:取消 月份:7 日期:1` — 取消指定月份的值班設定\n"
            "`/值班休假設定 類型:休假 動作:設定 月份:7 日期:10 11` — **重設**指定月份休假\n"
            "`/值班休假設定 類型:休假 動作:新增 月份:7 日期:12` — **追加**休假日，不影響同月現有設定\n"
            "`/值班休假設定 類型:休假 動作:取消 月份:7 日期:10` — 取消指定月份的休假設定\n"
            "（若前一天是值班日，仍會打值班下班卡）\n"
            "月底前設定下個月值班/休假日，會自動視為完成下月設定。"
        ),
        inline=False
    )
    embed.add_field(
        name="⏸️ 臨時調整",
        value=(
            "`/自動打卡取消` — 今天改為手動，不自動打卡（僅今天，明天自動恢復）\n"
            "`/自動打卡恢復` — 提前重新開啟自動打卡"
        ),
        inline=False
    )
    embed.add_field(
        name="🔔 自動通知說明",
        value=(
            "**① 早晨提醒**（每天 06:50）\n"
            "　今日預計打卡時間，確認 Bot 正常運作\n"
            "　值班隔天：只提醒值班下班時間，不提醒上班\n"
            "　休假日：不提醒（但休假且昨天值班仍會提醒值班下班）\n\n"
            "**② 打卡前提醒**（打卡前 10 分鐘）\n"
            "　提醒即將自動打卡，可使用 `/自動打卡取消` 取消當天\n"
            "　值班當天下午下班：不提醒\n"
            "　值班隔天早上上班：不提醒\n"
            "　週末非值班：不提醒\n\n"
            "**③ 下班比對**\n"
            "　平日 18:00 / 值班隔天 09:00：查詢 e-HR 記錄\n"
            "　e-HR 有記錄（index6 或 times）→ 不通知（打卡成功）\n"
            "　e-HR 無記錄但 Bot 已打卡 → 通知「e-HR 尚未記錄」+ 補打按鈕\n"
            "　完全無記錄 → 通知「未偵測到打卡」+ 補打按鈕\n\n"
            "**④ 月底摘要**（每月最後一天 19:00）\n"
            "　本月所有打卡記錄摘要，方便核對\n\n"
            "**⑤ 下月設定提醒**（月底前五天每天 08:00）\n"
            "　未完成下月設定者會收到私訊提醒；完成後不再提醒\n"
            "　每月最後一天 09:00 會發送完成/未完成名單給管理員\n\n"
            "**⑥ 補打按鈕通知**（不受通知設定影響，永遠發送）\n"
            "　以下情況會私訊發送補打確認按鈕：\n"
            "　• Bot 重啟：發現排程時間已過但未打下班卡\n"
            f"　• 打卡失敗：自動打卡失敗時同步發出，可選擇立即補打或等待 {RETRY_DELAY_MINUTES} 分鐘後自動重試\n"
            "　• 下班比對（18:00/09:00）：e-HR 無任何刷卡記錄時發出\n"
            "　⚠️ 上班卡僅 08:00 前補打；08:00 後只提醒，不自動補打\n"
            "　按鈕有效時間為 10 分鐘，逾時需手動打卡\n\n"
            "⚠️ **打卡成功/失敗通知不受通知設定影響，永遠發送**\n\n"
            "🔕 `/通知全關` — 一鍵關閉以上四種通知\n"
            "🔔 `/通知設定` — 個別開關各種通知"
        ),
        inline=False
    )
    embed.add_field(
        name="🔍 查詢功能",
        value=(
            "`/查今日狀態` — 查看今天的打卡模式、Bot 排程時間，以及與 e-HR 記錄的比對結果\n"
            "`/查詢本日e-hr刷卡記錄` — 直接查詢今天 e-HR 系統的刷卡記錄（未綁定者可輸入帳號密碼）\n"
            "`/查詢bot排程` — 查看本月或下月每天的 Bot 打卡排程\n"
            "`/查詢本月e-hr刷出卡紀錄` — 查詢本月 e-HR 紀錄的上下班時間（未綁定者可輸入帳號密碼；月底也會自動私訊發送）\n"
            "`/查詢值班休假日程` — 查看本月、下月、後續月份值班休假與下月設定狀態\n"
            "`/說明` — 顯示此說明頁面"
        ),
        inline=False
    )
    embed.set_footer(text="所有指令只有自己看得到 · 每天打卡時間在範圍內隨機產生")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── 查詢 Bot 自動打卡排程 ──
def add_months(base_date, month_delta):
    month_index = base_date.month - 1 + month_delta
    year = base_date.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)

def build_bot_schedule_embeds(user_data, uid, target_month_date):
    today = date.today()
    year = target_month_date.year
    month = target_month_date.month
    next_month = add_months(target_month_date, 1)
    days_in_month = (next_month - target_month_date).days

    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    lines = []
    duty_days = user_data.get("duty_days", [])
    leave_dates = user_data.get("leave_dates", [])
    cancel_dates = user_data.get("cancel_dates", [])

    today_times = scheduled_times.get(uid, {})
    time_in = today_times.get("in", f"{PUNCH_IN_START[0]:02d}:{PUNCH_IN_START[1]:02d}~{PUNCH_IN_END[0]:02d}:{PUNCH_IN_END[1]:02d}")
    time_out = today_times.get("out", f"{PUNCH_OUT_START[0]:02d}:{PUNCH_OUT_START[1]:02d}~{PUNCH_OUT_END[0]:02d}:{PUNCH_OUT_END[1]:02d}")
    time_duty = today_times.get("dutyout", f"{DUTY_OUT_START[0]:02d}:{DUTY_OUT_START[1]:02d}~{DUTY_OUT_END[0]:02d}:{DUTY_OUT_END[1]:02d}")
    default_in = f"{PUNCH_IN_START[0]:02d}:{PUNCH_IN_START[1]:02d}~{PUNCH_IN_END[0]:02d}:{PUNCH_IN_END[1]:02d}"
    default_out = f"{PUNCH_OUT_START[0]:02d}:{PUNCH_OUT_START[1]:02d}~{PUNCH_OUT_END[0]:02d}:{PUNCH_OUT_END[1]:02d}"
    default_duty = f"{DUTY_OUT_START[0]:02d}:{DUTY_OUT_START[1]:02d}~{DUTY_OUT_END[0]:02d}:{DUTY_OUT_END[1]:02d}"

    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        date_str = d.strftime("%Y-%m-%d")
        weekday = weekday_names[d.weekday()]
        day_label = f"{month:02d}/{day:02d}（{weekday}）"
        prefix = "▶" if d == today else "　"
        is_past = d < today
        is_today = d == today
        has_duty_yesterday = (d - timedelta(days=1)).strftime("%Y-%m-%d") in duty_days

        if date_str in leave_dates:
            if has_duty_yesterday:
                lines.append(f"{prefix}`{day_label}` 🏖️休假＋值班下班" + (" ✅" if is_past and not is_today else ""))
                if not is_past or is_today:
                    lines.append(f"　　　　　　⏰ 值班下班：{time_duty if is_today else default_duty}")
            else:
                lines.append(f"{prefix}`{day_label}` 🏖️ 休假（不打卡）")
        elif date_str in cancel_dates:
            lines.append(f"{prefix}`{day_label}` ⏸️ 取消自動打卡（手動）")
        elif date_str in duty_days:
            lines.append(f"{prefix}`{day_label}` 🌙 值班" + (" ✅" if is_past and not is_today else ""))
            if not is_past or is_today:
                lines.append(f"　　　　　　⏰ 上班：{time_in if is_today else default_in}")
        elif d.weekday() >= 5:
            if has_duty_yesterday:
                lines.append(f"{prefix}`{day_label}` 📴週末＋值班下班" + (" ✅" if is_past and not is_today else ""))
                if not is_past or is_today:
                    lines.append(f"　　　　　　⏰ 值班下班：{time_duty if is_today else default_duty}")
            else:
                lines.append(f"{prefix}`{day_label}` 📴 週末")
        else:
            if has_duty_yesterday:
                label = "🟢 平日（值班隔天）"
                lines.append(f"{prefix}`{day_label}` {label}" + (" ✅" if is_past and not is_today else ""))
                if not is_past or is_today:
                    lines.append(f"　　　　　　⏰ 值班下班：{time_duty if is_today else default_duty}")
            else:
                if is_today:
                    lines.append(f"{prefix}`{day_label}` 🟢 平日（今天）")
                    lines.append(f"　　　　　　⏰ 上班：{time_in}　下班：{time_out}")
                elif is_past:
                    lines.append(f"{prefix}`{day_label}` 🟢 平日 ✅")
                else:
                    lines.append(f"{prefix}`{day_label}` 🟢 平日")
                    lines.append(f"　　　　　　⏰ 上班：{default_in}　下班：{default_out}")

    mid = len(lines) // 2
    embed1 = discord.Embed(
        title=f"📆 {year}年{month}月 Bot 打卡排程（上半月）",
        description="\n".join(lines[:mid]) or "（無資料）",
        color=0x5865F2,
    )
    embed2 = discord.Embed(
        title=f"📆 {year}年{month}月 Bot 打卡排程（下半月）",
        description="\n".join(lines[mid:]) or "（無資料）",
        color=0x5865F2,
    )
    embed2.set_footer(text="🟢平日　🌙值班　🏖️休假　⏸️取消自動　📴週末　✅已過")
    return [embed1, embed2]

class BotScheduleMonthView(discord.ui.View):
    def __init__(self, requester_id, selected_offset=0):
        super().__init__(timeout=300)
        self.requester_id = str(requester_id)
        self.selected_offset = selected_offset
        for item in self.children:
            if item.custom_id == f"bot_schedule_month_{selected_offset}":
                item.disabled = True

    async def _show_month(self, interaction, month_offset):
        if str(interaction.user.id) != self.requester_id:
            await interaction.response.send_message("這不是你的查詢面板。", ephemeral=True)
            return
        user_data = get_user_data(interaction.user.id)
        target_month = add_months(date.today().replace(day=1), month_offset)
        await interaction.response.edit_message(
            embeds=build_bot_schedule_embeds(user_data, self.requester_id, target_month),
            view=BotScheduleMonthView(self.requester_id, month_offset),
        )

    @discord.ui.button(label="本月", style=discord.ButtonStyle.primary, custom_id="bot_schedule_month_0")
    async def current_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_month(interaction, 0)

    @discord.ui.button(label="下月", style=discord.ButtonStyle.primary, custom_id="bot_schedule_month_1")
    async def next_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_month(interaction, 1)

@tree.command(name="查詢bot排程", description="查看本月或下月的 Bot 打卡排程")
async def monthly_status(interaction: discord.Interaction):
    user_data = get_user_data(interaction.user.id)

    if not user_data.get("empid"):
        embed = discord.Embed(
            title="❌ 尚未綁定帳號",
            description="請先使用 `/帳號綁定`",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    uid = str(interaction.user.id)
    target_month = date.today().replace(day=1)
    await interaction.response.send_message(
        embeds=build_bot_schedule_embeds(user_data, uid, target_month),
        view=BotScheduleMonthView(uid, 0),
        ephemeral=True,
    )

class AdminBotScheduleMonthView(discord.ui.View):
    def __init__(self, actor_id, target_uid, target_label, selected_offset=0):
        super().__init__(timeout=300)
        self.actor_id = str(actor_id)
        self.target_uid = str(target_uid)
        self.target_label = target_label
        self.selected_offset = selected_offset
        for item in self.children:
            if item.custom_id == f"admin_bot_schedule_month_{selected_offset}":
                item.disabled = True

    async def _show_month(self, interaction, month_offset):
        if str(interaction.user.id) != self.actor_id:
            await interaction.response.send_message("這不是你的管理查詢面板。", ephemeral=True)
            return
        user_data = get_user_data(self.target_uid)
        target_month = add_months(date.today().replace(day=1), month_offset)
        await interaction.response.edit_message(
            content=f"對象：{self.target_label}",
            embeds=build_bot_schedule_embeds(user_data, self.target_uid, target_month),
            view=AdminBotScheduleMonthView(self.actor_id, self.target_uid, self.target_label, month_offset),
        )

    @discord.ui.button(label="本月", style=discord.ButtonStyle.primary, custom_id="admin_bot_schedule_month_0")
    async def current_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_month(interaction, 0)

    @discord.ui.button(label="下月", style=discord.ButtonStyle.primary, custom_id="admin_bot_schedule_month_1")
    async def next_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_month(interaction, 1)

@admin_group.command(name="bot排程查詢", description="管理員查詢指定使用者本月或下月 Bot 打卡排程")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(使用者="要查詢 Bot 排程的 Discord 使用者")
async def admin_bot_schedule_query(interaction: discord.Interaction, 使用者: discord.User):
    if await reject_non_admin(interaction):
        return
    user_data = get_user_data(使用者.id)
    if not user_data.get("empid"):
        embed = discord.Embed(
            title="❌ 尚未綁定帳號",
            description=f"對象：{使用者.mention}\n此使用者尚未綁定 e-HR 帳號。",
            color=0xff0000,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    target_month = date.today().replace(day=1)
    await interaction.response.send_message(
        content=f"對象：{使用者.mention}",
        embeds=build_bot_schedule_embeds(user_data, str(使用者.id), target_month),
        view=AdminBotScheduleMonthView(interaction.user.id, 使用者.id, 使用者.mention, 0),
        ephemeral=True,
    )

_auto_punch_started = False  # 確保 auto_punch_task 只啟動一次
_commands_synced = False
_discord_disconnected_at = None
_health_tasks_started = False
_persistent_views_registered = False
SYNC_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "synced.flag")

async def discord_disconnect_watchdog():
    """Exit a stuck Discord client so NSSM can restart it."""
    while not client.is_closed():
        await asyncio.sleep(30)
        if _discord_disconnected_at is None:
            continue
        offline_seconds = time.monotonic() - _discord_disconnected_at
        if offline_seconds >= DISCONNECT_RESTART_SECONDS:
            print(f"❌ Discord 已斷線 {int(offline_seconds)} 秒，結束程序交由 NSSM 重啟。")
            _log_file.flush()
            os._exit(3)

_lifecycle_startup_notice_sent = False


async def send_lifecycle_alert(message):
    """直接送到管理員告警頻道，不使用一般打卡告警去重機制。"""
    global _lifecycle_major_alert_pending

    print("Lifecycle Discord admin alert disabled; message logged only.")
    print(message)
    _lifecycle_major_alert_pending = False
    return

    if not _lifecycle_major_alert_pending:
        print("Suppressed non-critical Punch Relay lifecycle admin alert.")
        return
    _lifecycle_major_alert_pending = False

    if ADMIN_ALERT_CHANNEL_ID <= 0:
        print("⚠️ ADMIN_ALERT_CHANNEL_ID 未設定，無法發送 Bot 生命週期通知。")
        return

    try:
        channel = client.get_channel(ADMIN_ALERT_CHANNEL_ID)
        if channel is None:
            channel = await client.fetch_channel(ADMIN_ALERT_CHANNEL_ID)
        await channel.send(message)
    except Exception as e:
        print(f"⚠️ Bot 生命週期 Discord 通知失敗：{e}")

@client.event
async def on_ready():
    global _auto_punch_started
    global _commands_synced
    global _discord_disconnected_at
    global _health_tasks_started
    global _lifecycle_startup_notice_sent
    global _persistent_views_registered

    was_disconnected = _discord_disconnected_at is not None
    is_first_ready_of_this_process = not _lifecycle_startup_notice_sent

    _discord_disconnected_at = None
    if not _persistent_views_registered:
        client.add_view(PersistentNextMonthReminderView())
        _persistent_views_registered = True
        print("✅ 下月設定永久按鈕已註冊")

    # Only sync after first install or when restart_bot_resync.ps1 removes the flag.
    if not _commands_synced and not os.path.exists(SYNC_FLAG):
        try:
            await tree.sync()
            _commands_synced = True
            with open(SYNC_FLAG, "w", encoding="utf-8") as f:
                f.write(datetime.now().isoformat(timespec="seconds"))
            print("✅ 指令同步完成")
        except Exception as e:
            print(f"⚠️ 指令同步失敗：{e}")
    else:
        _commands_synced = True
        print("✅ 指令已同步（跳過）")
    print(f"✅ Bot 已啟動：{client.user}")

    # auto_punch_task 只在第一次 on_ready 時啟動，斷線重連不重複啟動
    # 每個 Python 程序第一次成功連上 Discord 時通知一次。
    if is_first_ready_of_this_process:
        _lifecycle_startup_notice_sent = True
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if _previous_run_unclean:
            previous_time = (
                _previous_runtime_state.get("updated_at", "未知時間")
                if _previous_runtime_state else "未知時間"
            )
            await send_lifecycle_alert(
                "⚠️ **Punch Relay 已恢復運作，但偵測到上次可能非正常中斷**\n"
                f"目前恢復時間：{now_text}\n"
                f"上次狀態更新：{previous_time}\n"
                "可能原因：電腦斷電、強制重開機、服務被強制停止、Python 崩潰，"
                "或 NSSM 因異常而重啟。"
            )
        else:
            await send_lifecycle_alert(
                "✅ **Punch Relay 已成功啟動並連上 Discord**\n"
                f"時間：{now_text}\n"
                "NSSM 服務與 Bot 目前均已恢復運作。"
            )
    elif was_disconnected:
        await send_lifecycle_alert(
            "🔄 **Punch Relay Discord 連線已恢復**\n"
            f"時間：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
            "可能是網路短暫中斷、電腦睡眠恢復，或 Discord 連線重新建立。"
        )

    if not _auto_punch_started:
        _auto_punch_started = True
        client.loop.create_task(auto_punch_task(client))
        print("✅ auto_punch_task 已啟動")
    else:
        print("🔄 Discord 重新連線，auto_punch_task 繼續運行中")

    if not _health_tasks_started:
        _health_tasks_started = True
        client.loop.create_task(discord_disconnect_watchdog())
        print("✅ Discord 斷線監控已啟動")

@client.event
async def on_disconnect():
    global _discord_disconnected_at
    if _discord_disconnected_at is None:
        _discord_disconnected_at = time.monotonic()
    print("⚠️ Discord 連線中斷，等待自動重連...")

# reconnect=True（預設）讓 discord.py 自動重連，log_handler=None 避免重複設定
if __name__ == "__main__":
    acquire_single_instance()

    # 先讀上一次狀態，再標記本次程序正在執行。
    mark_runtime_started()

    # 正常結束時才會標記 clean_exit。
    # 斷電、崩潰、os._exit()、被強制終止時不會執行，因此可被下次啟動偵測。
    atexit.register(mark_runtime_clean_exit)

    client.run(DISCORD_TOKEN, reconnect=True, log_handler=None)
