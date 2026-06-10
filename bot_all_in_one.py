import sys
import io
import os

# 背景執行（工作排程器）時沒有終端機，將 stdout/stderr 導向 log 檔案
# 同時解決 cp950 無法處理 emoji 的問題
try:
    _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")
    _log_file = open(_log_path, "a", encoding="utf-8", errors="replace")
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

# =====================
# 設定區（只需要改這裡）
# =====================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
NOTIFY_CHANNEL_ID = int(os.getenv("NOTIFY_CHANNEL_ID", "0"))

EHR_BASE = os.getenv("EHR_BASE", "")
PUNCH_URL = f"{EHR_BASE}/servlet/jform"
FILE_PARAM = "hrm6p_edu.pkg,hrm6p.pkg,hrm6p_out_M1.pkg,hrm6fw_edu.pkg,hrm6bw.pkg,hrm6aw_edu.pkg,hrm6jw_edu.pkg,hrm6p_out_M21.pkg"

DATA_FILE = "punch_data.json"

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
            "請確認 C:\\punch_relay\\.env 內已設定 DISCORD_TOKEN、NOTIFY_CHANNEL_ID、EHR_BASE。"
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
    return data[uid]

def save_user_data(user_id, user_data):
    data = load_data()
    data[str(user_id)] = user_data
    save_data(data)

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
        if "hrlogin" in login_resp.text:
            return {"success": False, "message": "登入失敗，請確認員工編號或密碼"}
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

# =====================
# 查詢今日打卡記錄
# =====================
def query_today_punch(session, empid):
    """查詢今天的刷卡記錄，使用已登入的 session"""
    import re as re_mod
    from html import unescape

    try:
        today = date.today()
        roc_year = today.year - 1911
        yymm = f"{roc_year}{today.month:02d}"
        query_file = "hrm6p_edu.pkg,hrm6p.pkg,hrm6p_out_M1.pkg,hrm6fw_edu.pkg,hrm6bw.pkg,hrm6aw_edu.pkg,hrm6jw_edu.pkg,hrm6p_out_M21.pkg"

        # 先 GET B9 頁面取得 enc
        b9_page = session.get(
            f"{PUNCH_URL}?file={query_file}&init_func=B9.%E8%80%83%E5%8B%A4%E5%BD%99%E7%B8%BD%E8%A1%A8.",
            timeout=10, verify=False
        )
        b9_html = unescape(b9_page.text)
        enc_match = re_mod.search(r'name=enc\s+value="([^"]+)"', b9_html)
        enc = enc_match.group(1) if enc_match else ""

        dept_match = re_mod.search(r"EMPID[^>]*value='([^']+)'", b9_html)
        if not dept_match:
            dept_match = re_mod.search(r"'([0-9]+)'", b9_html)
        dept_no = dept_match.group(1) if dept_match else ""

        # POST 查詢
        payload = {
            "time": str(int(time.time() * 1000)),
            "form_ajax": "1",
            "encodeURIComponent": "1",
            "FUNCTION_NAME": "B9.\u8003\u52e4\u5f59\u7e3d\u8868.",
            "act": "",
            "init_func": "",
            "flow_approve": "",
            "buttonid": "button2",
            "buttonlink": "Entry View",
            "fromlink": "Entry View",
            "table_data": "",
            "form_target": "",
            "menu_display_flag": "",
            "em_step": "0",
            "em_POSITION": "1",
            "file": query_file,
            "enc": enc,
            "user_id": empid,
            "CONDITION_B": f"+and+a.EMPID%3D%27{empid}%27",
            "showNOTCARD": "N",
            "YYMM": yymm,
            "SKIND": "B",
            "SDATE": f"{roc_year}{today.month:02d}01",
            "EDATE": f"{roc_year}{today.month:02d}30",
            "EMPID": empid,
        }

        resp = session.post(
            PUNCH_URL,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{PUNCH_URL}?file={query_file}",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15, verify=False
        )

        html = unescape(resp.text)

        # ── 找今天日期，相容補零與不補零格式（115/06/08 或 115/6/8）──
        target_pattern = re_mod.compile(
            rf'{roc_year}/0?{today.month}/0?{today.day}(?!\d)'
        )
        m = target_pattern.search(html)
        if not m:
            return {"success": False, "message": "今日尚無刷卡記錄"}

        # ── 從日期位置往前找最近的 <TR，確保從整行開始解析 ──
        date_idx = m.start()
        tr_match = None
        for tr_m in re_mod.finditer(r'<TR[^>]*>', html[:date_idx], re_mod.IGNORECASE):
            tr_match = tr_m
        if tr_match is None:
            return {"success": False, "message": "今日尚無刷卡記錄"}

        # 取該 TR 到下一個 TR 之間的內容（最多 3000 字元）
        row_start = tr_match.start()
        row_text = html[row_start:row_start + 3000]

        # ── 用 <TD 切割法正確解析各欄 ──
        # 欄位順序：部門/員工編號/姓名/日期/班別/刷卡/出卡/異常/請假/加班/刷卡資料/補刷卡
        # index:     0    1       2    3    4   /5  /6  / 7  / 8  / 9  /10        /11
        from html import unescape as _ue2

        td_parts = re_mod.split(r'<TD[^>]*>', row_text, flags=re_mod.IGNORECASE)
        td_texts = []
        for part in td_parts[1:]:
            text = re_mod.sub(r'<[^>]+>', '', part)
            text = _ue2(text).replace('&nbsp;', '').replace('\xa0', '').strip()
            td_texts.append(text)

        def _clean4(s):
            s = s.strip()
            return s if re_mod.fullmatch(r'\d{4}', s) else None

        # 主要來源：e-HR 系統判定欄（刷卡=index5，出卡=index6）
        raw_in_str  = td_texts[5] if len(td_texts) > 5 else ""
        raw_out_str = td_texts[6] if len(td_texts) > 6 else ""
        clock_in  = f"{raw_in_str[:2]}:{raw_in_str[2:]}"  if _clean4(raw_in_str)  else None
        clock_out = f"{raw_out_str[:2]}:{raw_out_str[2:]}" if _clean4(raw_out_str) else None

        # 刷卡資料欄（index10）：所有原始打卡記錄（逗號分隔）
        raw_times_str = td_texts[10].strip() if len(td_texts) > 10 else ""
        all_times = []
        if raw_times_str:
            for t in raw_times_str.split(','):
                t = t.strip()
                if re_mod.fullmatch(r'\d{4}', t):
                    h, mn = int(t[:2]), int(t[2:])
                    if 0 <= h <= 23 and 0 <= mn <= 59:
                        all_times.append(f"{t[:2]}:{t[2:]}")

        # 若刷卡資料欄只有單筆（不含逗號），嘗試直接解析
        if not all_times and re_mod.fullmatch(r'\d{4}', raw_times_str):
            h, mn = int(raw_times_str[:2]), int(raw_times_str[2:])
            if 0 <= h <= 23 and 0 <= mn <= 59:
                all_times.append(f"{raw_times_str[:2]}:{raw_times_str[2:]}")

        # 回傳原始欄位，不在此做情境推算
        # clock_in  = e-HR index5（人資系統判定上班時間，空代表尚未判定）
        # clock_out = e-HR index6（人資系統判定下班/退卡時間，空代表尚未判定）
        # times     = index10 所有原始刷卡記錄（排序後），供上層依情境推算
        if all_times or clock_in or clock_out:
            return {
                "success": True,
                "times": sorted(all_times),
                "clock_in": clock_in,
                "clock_out": clock_out,
            }

        return {"success": False, "message": "今日尚無刷卡記錄"}

    except Exception as e:
        return {"success": False, "message": f"查詢失敗：{str(e)}"}


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
        out_source   = "ehr"
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
            query_result = query_today_punch(session, empid)
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

def get_today_schedule(user_data):
    today = date.today()
    yesterday = today - timedelta(days=1)

    if not user_data.get("empid"):
        return "尚未綁定帳號\n請使用 `/帳號綁定` 開始設定"
    if not user_data.get("auto_punch", True):
        return "⚠️ 自動打卡已關閉\n請使用 `/自動打卡恢復` 開啟"
    if is_auto_cancelled(user_data, today):
        return "⏸️ 今日已取消自動打卡（手動模式）\n請使用 `/打卡` 手動打卡"

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
                "若有值班請使用 `/值班新增` 或手動 `/打卡`"
            ]
        if is_duty_day(user_data, yesterday):
            lines.append("")
            lines.append(f"⏰ {h_duty:02d}:{m_duty:02d}~{h_duty_e:02d}:{m_duty_e:02d} 自動打值班下班卡（昨日值班）")
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

    if is_duty_day(user_data, yesterday):
        lines.append("")
        lines.append(f"⏰ {h_duty:02d}:{m_duty:02d}~{h_duty_e:02d}:{m_duty_e:02d} 自動打值班下班卡（昨日值班）")

    return "\n".join(lines)

# 全域隨機時間表（讓查詢指令可以讀取）
scheduled_times = {}

PUNCHED_FILE = "punched_today.json"
SCHEDULE_FILE = "schedule_today.json"

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

        for uid, user_data in data.items():
            empid = user_data.get("empid")
            password = user_data.get("password")

            if not empid or not password:
                continue
            if not user_data.get("auto_punch", True):
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
                            # 上班卡失敗不發補打按鈕（八點後補打會變遲到）
                            rk = None
                            retry_at = datetime.now() + timedelta(minutes=5)
                            should_retry = action != "in" or retry_at < datetime.combine(today, datetime.min.time()) + timedelta(hours=8)
                            if action != "in":
                                rk = f"{uid}-dutyout-{today_str}" if label == "值班下班" else f"{uid}-out-{today_str}"
                            if should_retry:
                                retry_queue.append({
                                    "uid": uid,
                                    "empid": empid,
                                    "password": password,
                                    "action": action,
                                    "label": label,
                                    "retry_at": retry_at,
                                    "attempts": 1,
                                    "retry_key": rk,
                                })
                                fail_msg = f"🤖 自動打卡通知\n❌ {label}打卡失敗：{result.get('message')}\n⏳ 將於 5 分鐘後自動重試"
                            else:
                                fail_msg = f"🤖 自動打卡通知\n❌ {label}打卡失敗：{result.get('message')}\n⚠️ 已接近或超過 08:00，為避免遲到記錄，不自動重試上班卡"
                            await user.send(fail_msg)
                            if should_retry and action != "in" and rk:
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
                                    f"🤖 **補打確認**\n⚠️ {label}卡自動打卡失敗，是否立即補打？\n（也可等待 5 分鐘後自動重試）",
                                    view=makeup_view
                                )
                    except discord.Forbidden:
                        channel = client.get_channel(NOTIFY_CHANNEL_ID)
                        if channel:
                            await channel.send(f"⚠️ 無法私訊 <@{uid}>，請開啟私訊權限")
                    except Exception as e:
                        print(f"私訊失敗：{e}")

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
                    attempt_str = f"（第 {item['attempts']+1} 次嘗試）"
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
                        elif item["attempts"] < 3:
                            next_retry = now_dt + timedelta(minutes=5)
                            if item["action"] == "in" and next_retry >= datetime.combine(today, datetime.min.time()) + timedelta(hours=8):
                                retry_msg = f"🤖 重試打卡通知 {attempt_str}\n❌ 仍然失敗：{retry_result.get('message')}\n⚠️ 下一次重試會超過 08:00，為避免遲到記錄，已停止自動重試上班卡"
                            else:
                                still_retrying.append({**item, "retry_at": next_retry, "attempts": item["attempts"]+1})
                                retry_msg = f"🤖 重試打卡通知 {attempt_str}\n❌ 仍然失敗：{retry_result.get('message')}\n⏳ 將於 5 分鐘後再試"
                            await retry_user.send(retry_msg)
                            # 同步發補打按鈕（下班卡才發）
                            if item["action"] != "in" and rk:
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
                            retry_msg = f"🤖 重試打卡通知 {attempt_str}\n❌ 重試 3 次後仍失敗，請手動打卡\n原因：{retry_result.get('message')}"
                            await retry_user.send(retry_msg)
                            # 最終失敗也發一次補打按鈕
                            if item["action"] != "in" and rk:
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
                                    f"🤖 **補打確認**\n⚠️ {item['label']}卡已重試 3 次仍失敗，是否手動補打？",
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
                if is_weekend(today) or is_duty_day(ud_c, today) or is_leave_day(ud_c, today):
                    continue
                try:
                    def do_check(ep=empid_c, pw=password_c):
                        import requests as req
                        import urllib3 as ul3
                        ul3.disable_warnings()
                        sess = req.Session()
                        sess.get(f"{PUNCH_URL}?file={FILE_PARAM}", verify=False, timeout=10)
                        sess.post(PUNCH_URL, data={"file": FILE_PARAM, "uid": ep, "pwd": pw, "image.x": "0", "image.y": "0"},
                                  headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15, verify=False)
                        query_file = "hrm6p_edu.pkg,hrm6p.pkg,hrm6p_out_M1.pkg,hrm6fw_edu.pkg,hrm6bw.pkg,hrm6aw_edu.pkg,hrm6jw_edu.pkg,hrm6p_out_M21.pkg"
                        sess.get(f"{PUNCH_URL}?file={query_file}&init_func=B9.%E8%80%83%E5%8B%A4%E5%BD%99%E7%B8%BD%E8%A1%A8.", verify=False, timeout=10)
                        return query_today_punch(sess, ep)
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
                        else:
                            notify_msg = f"📋 **今日下班打卡確認（18:00）**\n⚠️ 未偵測到任何下班打卡記錄\n請確認是否需要補打"
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
                if not empid_c2 or not password_c2 or not ud_c2.get("auto_punch", True):
                    continue
                if not ud_c2.get("notify", {}).get("compare", True):
                    continue
                if not is_duty_day(ud_c2, yesterday):
                    continue
                try:
                    def do_check2(ep=empid_c2, pw=password_c2):
                        import requests as req
                        import urllib3 as ul3
                        ul3.disable_warnings()
                        sess = req.Session()
                        sess.get(f"{PUNCH_URL}?file={FILE_PARAM}", verify=False, timeout=10)
                        sess.post(PUNCH_URL, data={"file": FILE_PARAM, "uid": ep, "pwd": pw, "image.x": "0", "image.y": "0"},
                                  headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15, verify=False)
                        query_file = "hrm6p_edu.pkg,hrm6p.pkg,hrm6p_out_M1.pkg,hrm6fw_edu.pkg,hrm6bw.pkg,hrm6aw_edu.pkg,hrm6jw_edu.pkg,hrm6p_out_M21.pkg"
                        sess.get(f"{PUNCH_URL}?file={query_file}&init_func=B9.%E8%80%83%E5%8B%A4%E5%BD%99%E7%B8%BD%E8%A1%A8.", verify=False, timeout=10)
                        return query_today_punch(sess, ep)
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
                        else:
                            notify_msg2 = f"📋 **今日值班下班打卡確認（09:00）**\n⚠️ 未偵測到任何值班下班打卡記錄\n請確認是否需要補打"
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

        # ── 每月最後一天 22:00 發送本月打卡摘要 ──
        if current_time == "22:00":
            next_day = today + timedelta(days=1)
            if next_day.month != today.month:  # 今天是本月最後一天
                data_summary = load_data()
                for uid_s, ud_s in data_summary.items():
                    if not ud_s.get("empid"):
                        continue
                    empid_s = ud_s.get("empid")
                    password_s = ud_s.get("password")
                    if not empid_s or not password_s:
                        continue
                    try:
                        if not ud_s.get("notify", {}).get("monthly", True):
                            continue
                        summary_result = await asyncio.get_event_loop().run_in_executor(
                            None, lambda ep=empid_s, pw=password_s: _query_monthly_summary(ep, pw)
                        )
                        try:
                            summary_user = client.get_user(int(uid_s)) or await client.fetch_user(int(uid_s))
                            await summary_user.send(summary_result)
                        except Exception as e:
                            print(f"月底摘要私訊失敗 {uid_s}：{e}")
                    except Exception as e:
                        print(f"月底摘要查詢失敗 {uid_s}：{e}")

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
            "EDATE": f"{roc_year}{today.month:02d}30",
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

        def clean_time(s):
            s = s.strip()
            return s if re.fullmatch(r'\d{4}', s) else None

        raw_in  = td_texts[5] if len(td_texts) > 5 else ""
        raw_out = td_texts[6] if len(td_texts) > 6 else ""
        abnormal = td_texts[7].strip() if len(td_texts) > 7 else ""
        raw_times_str = td_texts[10].strip() if len(td_texts) > 10 else ""

        clock_in  = f"{raw_in[:2]}:{raw_in[2:]}"   if clean_time(raw_in)  else None
        clock_out = f"{raw_out[:2]}:{raw_out[2:]}"  if clean_time(raw_out) else None

        raw_times = []
        if raw_times_str:
            for t in raw_times_str.split(','):
                t = t.strip()
                if re.fullmatch(r'\d{4}', t):
                    raw_times.append(f"{t[:2]}:{t[2:]}")

        records.append({
            "date": roc_date,
            "in": clock_in,
            "out": clock_out,
            "abnormal": abnormal,
            "raw_times": raw_times,
        })
    return records

def _query_monthly_summary(empid, password, user_data=None):
    """查詢月底摘要，回傳格式化字串"""
    today = date.today()
    html = _query_monthly_b9(empid, password)
    if not html:
        return f"🤖 **{today.month}月打卡摘要**\n\n❌ 無法取得 e-HR 資料，請手動確認"
    records = _parse_monthly_records(html)
    if not records:
        return f"🤖 **{today.month}月打卡摘要**\n\n查無本月打卡記錄"

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

    for r in records:
        d = r["date"]
        ci = r["in"]
        co = r["out"]
        abnormal = r.get("abnormal", "")

        ci_str = ci or "—"
        co_str = co or "—"

        # 判斷是否在範圍內
        ci_ok = in_range(ci, in_start, in_end)
        co_ok = in_range(co, out_start, out_end)

        # 上班時間標記
        if ci and ci_ok is True:
            ci_label = f"{ci_str}✅"
        elif ci and ci_ok is False:
            ci_label = f"{ci_str}⚠️"
        else:
            ci_label = ci_str

        # 下班時間標記（也檢查值班下班範圍）
        co_ok2 = in_range(co, duty_start, duty_end)
        if co and (co_ok is True or co_ok2 is True):
            co_label = f"{co_str}✅"
        elif co and co_ok is False and co_ok2 is False:
            co_label = f"{co_str}⚠️"
        else:
            co_label = co_str

        if abnormal:
            lines.append(f"⚠️ {d}　上班 {ci_label}　下班 {co_label}　{abnormal}")
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

    lines.append(f"\n📊 共 {ok_count} 天正常，{miss_count} 天異常/缺卡")
    lines.append(f"✅=在Bot範圍內　⚠️=超出範圍或異常")
    return "\n".join(lines)

# =====================
# Discord Bot
# =====================
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ── 補打卡確認 View（需使用者按鈕確認才打卡）──
class MakeupPunchView(discord.ui.View):
    def __init__(self, client_ref, uid, empid, password, action, label, punch_key, punched_today_ref, today_str, retry_key=None):
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
async def new_user_punch_assessment(discord_user, uid_str, empid, password, user_data):
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

    # ── 情境判斷 ──
    # 週末非值班、休假非值班隔天 → 不處理
    if (is_wknd and not is_duty_today and not is_duty_after):
        return
    if (is_leave_today and not is_duty_after):
        return

    try:
        # 登入查詢 e-HR
        def do_assess(ep=empid, pw=password):
            import requests as req
            import urllib3 as ul3
            ul3.disable_warnings()
            sess = req.Session()
            sess.get(f"{PUNCH_URL}?file={FILE_PARAM}", verify=False, timeout=10)
            sess.post(PUNCH_URL,
                      data={"file": FILE_PARAM, "uid": ep, "pwd": pw, "image.x": "0", "image.y": "0"},
                      headers={"Content-Type": "application/x-www-form-urlencoded"},
                      timeout=15, verify=False)
            return query_today_punch(sess, ep)

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
                        )
                    ))

        # ── 發送訊息 ──
        for msg in msgs:
            await discord_user.send(msg)
        for msg, view in views:
            await discord_user.send(msg, view=view)

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
            user_data["auto_punch"] = True
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
                    "如有值班請使用 `/值班新增` 設定\n\n"
                    "🔍 正在確認今日打卡狀況，稍後若有需要將私訊通知..."
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

# ── 模式切換 ──
@tree.command(name="值班設定", description="重設整月值班日（會清空舊設定），例如：6/7 6/14 6/21")
@app_commands.describe(日期="輸入所有值班日期，用空格分隔，例如：6/7 6/14 6/21")
async def set_duty(interaction: discord.Interaction, 日期: str):
    user_data = get_user_data(interaction.user.id)
    year = date.today().year
    added = []
    failed = []

    # 先清空所有值班日
    user_data["duty_days"] = []

    for d in 日期.split():
        try:
            parts = d.replace("／", "/").split("/")
            if len(parts) == 2:
                month, day = int(parts[0]), int(parts[1])
                duty_date = date(year, month, day)
                date_str = duty_date.strftime("%Y-%m-%d")
                if date_str not in user_data["duty_days"]:
                    user_data["duty_days"].append(date_str)
                added.append(f"{month}/{day}")
        except:
            failed.append(d)

    save_user_data(interaction.user.id, user_data)

    desc = "⚠️ 原有值班設定已清空，重新設定為：\n\n"
    if added:
        desc += f"✅ 值班日：{', '.join(added)}\n\n"
        desc += "值班打卡時間：\n⏰ 當天 07:00~07:40 上班卡（隨機）\n⏰ 隔天 08:05~08:40 下班卡（隨機）"
    else:
        desc += "（無設定任何值班日）"
    if failed:
        desc += f"\n\n❌ 以下日期格式有誤：{', '.join(failed)}\n格式範例：6/7 6/14"

    embed = discord.Embed(title="🌙 值班日重設", description=desc, color=0x9b59b6)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="值班新增", description="新增單天值班，例如：6/21")
@app_commands.describe(日期="輸入值班日期，用空格分隔，例如：6/21 6/28")
async def add_duty(interaction: discord.Interaction, 日期: str):
    user_data = get_user_data(interaction.user.id)
    year = date.today().year
    added = []
    failed = []

    for d in 日期.split():
        try:
            parts = d.replace("／", "/").split("/")
            if len(parts) == 2:
                month, day = int(parts[0]), int(parts[1])
                duty_date = date(year, month, day)
                date_str = duty_date.strftime("%Y-%m-%d")
                if date_str not in user_data["duty_days"]:
                    user_data["duty_days"].append(date_str)
                added.append(f"{month}/{day}")
        except:
            failed.append(d)

    save_user_data(interaction.user.id, user_data)

    desc = ""
    if added:
        desc += f"✅ 已新增值班日：{', '.join(added)}\n\n"
        desc += "值班打卡時間：\n⏰ 當天 07:00~07:40 上班卡（隨機）\n⏰ 隔天 08:05~08:40 下班卡（隨機）"
    if failed:
        desc += f"\n\n❌ 以下日期格式有誤：{', '.join(failed)}\n格式範例：6/21"

    embed = discord.Embed(title="🌙 新增值班日", description=desc, color=0x9b59b6)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="值班取消", description="取消某天的值班設定，例如：6/1 6/8")
@app_commands.describe(日期="輸入要取消的值班日期，用空格分隔")
async def cancel_duty(interaction: discord.Interaction, 日期: str):
    user_data = get_user_data(interaction.user.id)
    year = date.today().year
    removed = []
    failed = []

    for d in 日期.split():
        try:
            parts = d.replace("／", "/").split("/")
            if len(parts) == 2:
                month, day = int(parts[0]), int(parts[1])
                date_str = date(year, month, day).strftime("%Y-%m-%d")
                if date_str in user_data["duty_days"]:
                    user_data["duty_days"].remove(date_str)
                    removed.append(f"{month}/{day}")
                else:
                    failed.append(f"{month}/{day}（不在值班清單）")
        except:
            failed.append(d)

    save_user_data(interaction.user.id, user_data)

    desc = ""
    if removed:
        desc += f"✅ 已取消值班日：{', '.join(removed)}\n"
    if failed:
        desc += f"⚠️ {', '.join(failed)}"

    embed = discord.Embed(title="🗑️ 取消值班日", description=desc, color=0xffaa00)
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
    user_data["auto_punch"] = True
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

    if not user_data.get("auto_punch", True):
        embed = discord.Embed(title=f"📋 今日打卡狀態｜{date_str}", description="⚠️ 自動打卡已關閉\n請使用 `/自動打卡恢復` 開啟", color=0xffaa00)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if is_auto_cancelled(user_data, today):
        embed = discord.Embed(title=f"📋 今日打卡狀態｜{date_str}", description="⏸️ 今日已取消自動打卡（手動模式）", color=0xffaa00)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if is_leave_day(user_data, today) and not is_duty_day(user_data, yesterday):
        embed = discord.Embed(title=f"📋 今日打卡狀態｜{date_str}", description="🏖️ 今日為休假日，不會自動打卡", color=0xffaa00)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # 查詢 e-HR 實際刷卡記錄
    loop = asyncio.get_event_loop()
    def do_status_query():
        import requests as req
        import urllib3 as ul3
        ul3.disable_warnings()
        sess = req.Session()
        sess.get(f"{PUNCH_URL}?file={FILE_PARAM}", verify=False, timeout=10)
        sess.post(PUNCH_URL, data={
            "file": FILE_PARAM, "uid": empid, "pwd": password,
            "image.x": "0", "image.y": "0"
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15, verify=False)
        query_file = "hrm6p_edu.pkg,hrm6p.pkg,hrm6p_out_M1.pkg,hrm6fw_edu.pkg,hrm6bw.pkg,hrm6aw_edu.pkg,hrm6jw_edu.pkg,hrm6p_out_M21.pkg"
        sess.get(f"{PUNCH_URL}?file={query_file}&init_func=B9.%E8%80%83%E5%8B%A4%E5%BD%99%E7%B8%BD%E8%A1%A8.", verify=False, timeout=10)
        return query_today_punch(sess, empid)

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
    if is_weekend(today) and is_duty_day(user_data, today):
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
        if eff_out and out_source == "ehr":
            lines.append(f"✅ 已在 **{eff_out}** 打卡值班下班")
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
        if eff_out and out_source == "ehr":
            lines.append(f"✅ 已在 **{eff_out}** 打卡下班")
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
            if source == "ehr":
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

# ── 休假功能 ──
@tree.command(name="休假設定", description="重設整月休假日（會清空舊設定），例如：6/23 6/24 6/25")
@app_commands.describe(日期="輸入所有休假日期，用空格分隔，例如：6/23 6/24")
async def set_leave(interaction: discord.Interaction, 日期: str):
    user_data = get_user_data(interaction.user.id)
    if "leave_dates" not in user_data:
        user_data["leave_dates"] = []
    year = date.today().year
    added = []
    failed = []

    # 先清空所有休假日
    user_data["leave_dates"] = []

    for d in 日期.split():
        try:
            parts = d.replace("／", "/").split("/")
            if len(parts) == 2:
                month, day = int(parts[0]), int(parts[1])
                leave_date = date(year, month, day)
                date_str = leave_date.strftime("%Y-%m-%d")
                if date_str not in user_data["leave_dates"]:
                    user_data["leave_dates"].append(date_str)
                added.append(f"{month}/{day}")
        except:
            failed.append(d)

    save_user_data(interaction.user.id, user_data)
    desc = "⚠️ 原有休假設定已清空，重新設定為：\n\n"
    if added:
        desc += f"✅ 休假日：{', '.join(added)}\n當天不會自動打卡"
    else:
        desc += "（無設定任何休假日）"
    if failed:
        desc += f"\n\n❌ 以下日期格式有誤：{', '.join(failed)}\n格式範例：6/23 6/24"
    embed = discord.Embed(title="🏖️ 休假日重設", description=desc, color=0x3498db)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="休假新增", description="新增單天休假，例如：6/23")
@app_commands.describe(日期="輸入休假日期，用空格分隔，例如：6/23 6/24")
async def add_leave(interaction: discord.Interaction, 日期: str):
    user_data = get_user_data(interaction.user.id)
    if "leave_dates" not in user_data:
        user_data["leave_dates"] = []
    year = date.today().year
    added = []
    failed = []

    for d in 日期.split():
        try:
            parts = d.replace("／", "/").split("/")
            if len(parts) == 2:
                month, day = int(parts[0]), int(parts[1])
                leave_date = date(year, month, day)
                date_str = leave_date.strftime("%Y-%m-%d")
                if date_str not in user_data["leave_dates"]:
                    user_data["leave_dates"].append(date_str)
                added.append(f"{month}/{day}")
        except:
            failed.append(d)

    save_user_data(interaction.user.id, user_data)
    desc = ""
    if added:
        desc += f"✅ 已新增休假日：{', '.join(added)}\n當天不會自動打卡"
    if failed:
        desc += f"\n\n❌ 以下日期格式有誤：{', '.join(failed)}\n格式範例：6/23"
    embed = discord.Embed(title="🏖️ 新增休假日", description=desc, color=0x3498db)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="休假取消", description="取消某天的休假設定，例如：6/10 6/11")
@app_commands.describe(日期="輸入要取消的休假日期，用空格分隔")
async def cancel_leave(interaction: discord.Interaction, 日期: str):
    user_data = get_user_data(interaction.user.id)
    if "leave_dates" not in user_data:
        user_data["leave_dates"] = []
    year = date.today().year
    removed = []
    failed = []

    for d in 日期.split():
        try:
            parts = d.replace("／", "/").split("/")
            if len(parts) == 2:
                month, day = int(parts[0]), int(parts[1])
                date_str = date(year, month, day).strftime("%Y-%m-%d")
                if date_str in user_data["leave_dates"]:
                    user_data["leave_dates"].remove(date_str)
                    removed.append(f"{month}/{day}")
                else:
                    failed.append(f"{month}/{day}（不在休假清單）")
        except:
            failed.append(d)

    save_user_data(interaction.user.id, user_data)
    desc = ""
    if removed:
        desc += f"✅ 已取消休假日：{', '.join(removed)}\n"
    if failed:
        desc += f"⚠️ {', '.join(failed)}"
    embed = discord.Embed(title="🗑️ 取消休假日", description=desc, color=0xffaa00)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="查詢值班休假日程", description="查看所有值班和休假日期")
async def duty_leave_list(interaction: discord.Interaction):
    user_data = get_user_data(interaction.user.id)
    today = date.today()
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]

    duties = sorted(user_data.get("duty_days", []))
    leaves = sorted(user_data.get("leave_dates", []))

    today_str = today.strftime("%Y-%m-%d")
    duty_future = [d for d in duties if d >= today_str]
    leave_future = [d for d in leaves if d >= today_str]
    duty_past = [d for d in duties if d < today_str]
    leave_past = [d for d in leaves if d < today_str]

    lines = []

    lines.append("**🌙 即將值班：**")
    if duty_future:
        for d in duty_future:
            dt = date.fromisoformat(d)
            wd = weekday_names[dt.weekday()]
            lines.append(f"　{dt.month}/{dt.day}（{wd}）")
    else:
        lines.append("　目前無設定")

    lines.append("")
    lines.append("**🏖️ 即將休假：**")
    if leave_future:
        for d in leave_future:
            dt = date.fromisoformat(d)
            wd = weekday_names[dt.weekday()]
            lines.append(f"　{dt.month}/{dt.day}（{wd}）")
    else:
        lines.append("　目前無設定")

    if duty_past or leave_past:
        lines.append("")
        lines.append("**📋 過去紀錄（最近5筆）：**")
        all_past = sorted(
            [(d, "🌙值班") for d in duty_past] +
            [(d, "🏖️請假") for d in leave_past]
        )
        for d, label in all_past[-5:]:
            dt = date.fromisoformat(d)
            wd = weekday_names[dt.weekday()]
            lines.append(f"　{label} {dt.month}/{dt.day}（{wd}）")
        if len(all_past) > 5:
            lines.append(f"　...等共 {len(all_past)} 筆")

    embed = discord.Embed(
        title="📅 值班與休假日程",
        description="\n".join(lines),
        color=0x9b59b6
    )
    embed.set_footer(text="使用 /值班新增 或 /休假新增 追加")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── 查詢打卡記錄 ──
@tree.command(name="查詢本日打卡記錄", description="查詢今天的 e-HR 刷卡記錄（未綁定者請填入員工編號和密碼）")
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
                "1️⃣ **已綁定帳號**：直接輸入 `/查詢本日打卡記錄` 即可\n"
                "2️⃣ **未綁定帳號**：輸入 `/查詢本日打卡記錄 員工編號:12345 密碼:yourpass`"
            ),
            color=0xffaa00
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    try:
        loop = asyncio.get_event_loop()

        def do_query():
            import requests as req
            import urllib3 as ul3
            ul3.disable_warnings()
            sess = req.Session()

            # 步驟1：GET 首頁取得初始 cookie
            sess.get(f"{PUNCH_URL}?file={FILE_PARAM}", verify=False, timeout=10)

            # 步驟2：POST 登入
            login_payload = {
                "file": FILE_PARAM,
                "uid": empid,
                "pwd": password,
                "image.x": "0",
                "image.y": "0",
            }
            sess.post(
                PUNCH_URL,
                data=login_payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15, verify=False
            )

            # 步驟3：GET B9 頁面（跟測試檔一樣的流程）
            query_file = "hrm6p_edu.pkg,hrm6p.pkg,hrm6p_out_M1.pkg,hrm6fw_edu.pkg,hrm6bw.pkg,hrm6aw_edu.pkg,hrm6jw_edu.pkg,hrm6p_out_M21.pkg"
            sess.get(
                f"{PUNCH_URL}?file={query_file}&init_func=B9.%E8%80%83%E5%8B%A4%E5%BD%99%E7%B8%BD%E8%A1%A8.",
                verify=False, timeout=10
            )

            return query_today_punch(sess, empid)

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
            times_list = result.get("times", [])

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
                    desc_lines.append(f"**上班：** {eff_in}（e-HR 刷卡時間）")
                elif eff_in and in_source == "times":
                    desc_lines.append(f"**上班：** {eff_in}（e-HR 有記錄但刷卡/出卡時間待定）")
            else:
                # 平日
                if eff_in and in_source == "ehr":
                    desc_lines.append(f"**上班：** {eff_in}（e-HR 刷卡時間）")
                elif eff_in and in_source == "times":
                    desc_lines.append(f"**上班：** {eff_in}（e-HR 有記錄但刷卡/出卡時間待定）")

            # ── 下班欄 ──
            if is_duty_day(user_data_for_schedule, today):
                desc_lines.append("**下班：** 值班日，不打下班卡")
                desc_lines.append(f"　↳ 明天 **{user_schedule.get('dutyout', '08:05~08:40')}** 自動打值班下班卡")
            elif is_duty_after:
                if eff_out and out_source == "ehr":
                    desc_lines.append(f"**值班下班：** {eff_out}（e-HR 刷退時間）")
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
                if eff_out and out_source == "ehr":
                    desc_lines.append(f"**下班：** {eff_out}（e-HR 刷退時間）")
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
@tree.command(name="查詢本月上下班紀錄", description="查詢本月 e-HR 紀錄的上下班時間，未綁定者可輸入員工編號和密碼查詢")
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
                "1️⃣ **已綁定帳號**：直接輸入 `/查詢本月上下班紀錄` 即可\n"
                "2️⃣ **未綁定帳號**：輸入 `/查詢本月上下班紀錄 員工編號:12345 密碼:yourpass`"
            ),
            color=0xffaa00
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    loop = asyncio.get_event_loop()
    summary_text = await loop.run_in_executor(None, lambda: _query_monthly_summary(empid, password))
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

@tree.command(name="管理帳號", description="查看所有綁定使用者的狀態")
@app_commands.default_permissions(administrator=True)
async def admin_query(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_IDS:
        embed = discord.Embed(title="❌ 權限不足", description="此指令僅限管理員使用", color=0xff0000)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    data = load_data()
    if not data:
        embed = discord.Embed(title="📋 使用者狀態", description="目前無任何綁定使用者", color=0xffaa00)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    lines = []
    today = date.today()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    month_str = today.strftime("%Y-%m")
    now_hm = datetime.now().strftime("%H:%M")
    punched_now = load_punched_today()
    saved_schedules = load_schedule_today()
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]

    def fmt_dates(date_list):
        parts = []
        for ds in date_list:
            try:
                dt = date.fromisoformat(ds)
                wd = weekday_names[dt.weekday()]
                parts.append(f"{dt.month}/{dt.day}（{wd}）")
            except:
                parts.append(ds)
        return "、".join(parts) if parts else "無"

    def scheduled(uid, kind):
        user_schedule = saved_schedules.get(uid) or scheduled_times.get(uid, {})
        return user_schedule.get(kind, "")

    def punched_time(uid, kind):
        return punched_now.get(f"{uid}-{kind}-{today_str}")

    def done_or_wait(uid, kind, label, schedule_time):
        punched_at = punched_time(uid, kind)
        if punched_at is not None:
            return f"✅ {label}：{punched_at or schedule_time or '已打卡'}"
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

        all_duties = sorted(ud.get("duty_days", []))
        all_leaves = sorted(ud.get("leave_dates", []))

        # 本月值班
        month_duties = [d for d in all_duties if d.startswith(month_str)]
        # 本月休假
        month_leaves = [d for d in all_leaves if d.startswith(month_str)]
        duty_detail = fmt_dates(month_duties)
        leave_detail = fmt_dates(month_leaves)

        is_duty_today = is_duty_day(ud, today)
        is_duty_after = is_duty_day(ud, yesterday)
        is_leave_today = is_leave_day(ud, today)
        is_weekend_today = is_weekend(today)
        is_cancelled_today = is_auto_cancelled(ud, today)

        scheduled_in = scheduled(uid, "in")
        scheduled_out = scheduled(uid, "out")
        scheduled_duty = scheduled(uid, "dutyout")

        today_lines = []
        if not ud.get("auto_punch", True):
            mode = "❌ 自動打卡關閉"
            today_lines.append("　今日打卡：不會自動執行")
        elif is_cancelled_today:
            mode = "⏸️ 今日已取消自動打卡"
            today_lines.append("　今日打卡：手動模式")
        elif is_duty_after:
            mode = "🌙 昨日值班後"
            today_lines.append("　上班：⏭️ 值班隔天不打上班卡")
            today_lines.append("　" + done_or_wait(uid, "dutyout", "值班下班", scheduled_duty))
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
            today_lines.append("　" + done_or_wait(uid, "in", "上班", scheduled_in))
            today_lines.append("　下班：⏭️ 今日值班不打下班卡")
            today_lines.append(f"　值班下班：明天 {scheduled_duty or '08:05~08:40'}")
        else:
            mode = "🟢 平日"
            today_lines.append("　" + done_or_wait(uid, "in", "上班", scheduled_in))
            today_lines.append("　" + done_or_wait(uid, "out", "下班", scheduled_out))

        lines.append(
            f"👤 <@{uid}>　員工編號：{empid}　自動打卡：{auto}\n"
            f"　今日模式：{mode}\n"
            + "\n".join(today_lines) + "\n"
            f"　**本月值班**（{len(month_duties)} 天）：{duty_detail}\n"
            f"　**本月休假**（{len(month_leaves)} 天）：{leave_detail}"
        )
    embed = discord.Embed(
        title=f"📋 所有綁定使用者（共 {len(lines)} 人）｜{today_str}",
        description="\n\n".join(lines) if lines else "無綁定使用者",
        color=0x5865F2
    )
    embed.set_footer(text=f"查詢時間：{datetime.now().strftime('%H:%M')}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="管理今日打卡驗證", description="管理員查詢所有綁定使用者今日 e-HR 實際刷卡紀錄")
@app_commands.default_permissions(administrator=True)
async def admin_verify_today(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_IDS:
        embed = discord.Embed(title="❌ 權限不足", description="此指令僅限管理員使用", color=0xff0000)
        await interaction.response.send_message(embed=embed, ephemeral=True)
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

    def ehr_query_one(empid, password):
        if not password:
            return {"success": False, "message": "未儲存密碼，無法登入 e-HR"}
        try:
            sess = requests.Session()
            sess.get(f"{PUNCH_URL}?file={FILE_PARAM}", timeout=10, verify=False)
            login_resp = sess.post(
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
            if "hrlogin" in login_resp.text:
                return {"success": False, "message": "登入失敗，請確認帳密"}
            return query_today_punch(sess, empid)
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
        if source == "times":
            return "e-HR 原始刷卡"
        return "e-HR"

    def actual_line(label, actual_t, source):
        if actual_t:
            return f"✅ {label}：{actual_t}（{source_text(source)}）"
        return f"⚠️ {label}：e-HR 尚無紀錄"

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
            or is_leave_today
            or (is_weekend_today and not is_duty_today)
        )

        detail_lines = [f"👤 <@{uid}>　員工編號：{empid}", f"　今日模式：{mode}"]
        if result.get("success"):
            inferred = infer_punch_times(result, is_duty_after)
            times = result.get("times", [])
            raw_times = "、".join(times) if times else "無"

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
        else:
            message = result.get("message", "今日尚無刷卡記錄")
            if no_auto_expected and "今日尚無刷卡記錄" in message:
                detail_lines.append("　✅ e-HR 今日無刷卡紀錄（符合今日模式）")
            else:
                detail_lines.append(f"　⚠️ e-HR 查詢結果：{message}")

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
    app_commands.Choice(name="月底摘要（每月最後一天22:00發送）", value="monthly"),
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
        value="`/帳號綁定` — 綁定員工編號+密碼，開啟自動打卡\n　　　　　　綁定後自動評估當天打卡狀況：\n　　　　　　• 排程時間已過且 e-HR 無記錄 → 私訊補打按鈕\n　　　　　　• 超過 08:00 未打上班卡 → 私訊告知（不補打，避免遲到）\n　　　　　　• 排程時間未到 → 等待自動打卡，不另通知\n`/帳號解除` — 取消綁定，停止自動打卡",
        inline=False
    )
    embed.add_field(
        name="📅 值班設定",
        value=(
            "`/值班設定 6/7 6/14 6/21` — **重設**整月值班（清空舊設定後重新設定，適合月初一次設定）\n"
            "`/值班新增 6/28` — **追加**單天值班，不影響現有設定（適合臨時加班）\n"
            "`/值班取消 6/7` — 取消某天的值班設定"
        ),
        inline=False
    )
    embed.add_field(
        name="🏖️ 休假設定",
        value=(
            "`/休假設定 6/23 6/24` — **重設**整月休假（清空舊設定後重新設定，適合月初一次設定）\n"
            "（若前一天是值班日，仍會打值班下班卡）\n"
            "`/休假新增 6/25` — **追加**單天休假，不影響現有設定（適合臨時休假）\n"
            "`/休假取消 6/23` — 取消某天的休假設定"
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
            "**④ 月底摘要**（每月最後一天 22:00）\n"
            "　本月所有打卡記錄摘要，方便核對\n\n"
            "**⑤ 補打按鈕通知**（不受通知設定影響，永遠發送）\n"
            "　以下情況會私訊發送補打確認按鈕：\n"
            "　• Bot 重啟：發現排程時間已過但未打下班卡\n"
            "　• 打卡失敗：自動打卡失敗時同步發出，可選擇立即補打或等待自動重試\n"
            "　• 下班比對（18:00/09:00）：e-HR 無任何刷卡記錄時發出\n"
            "　⚠️ 上班卡絕對不補打（八點後補打會造成遲到記錄）\n"
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
            "`/查詢本日打卡記錄` — 直接查詢今天 e-HR 系統的刷卡記錄（未綁定者可輸入帳號密碼）\n"
            "`/查詢本月打卡時間` — 查看整個月每天的打卡設定一覽\n"
            "`/查詢本月上下班紀錄` — 查詢本月 e-HR 紀錄的上下班時間（未綁定者可輸入帳號密碼；月底也會自動私訊發送）\n"
            "`/查詢值班休假日程` — 查看所有值班和休假日期\n"
            "`/說明` — 顯示此說明頁面"
        ),
        inline=False
    )
    embed.set_footer(text="所有指令只有自己看得到 · 每天打卡時間在範圍內隨機產生")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── 查詢本月自動打卡時間 ──
@tree.command(name="查詢本月打卡時間", description="查看整個月的打卡設定一覽")
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

    today = date.today()
    year = today.year
    month = today.month
    uid = str(interaction.user.id)

    # 計算本月天數
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    days_in_month = (next_month - date(year, month, 1)).days

    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    lines = []

    duty_days = user_data.get("duty_days", [])
    leave_dates = user_data.get("leave_dates", [])
    cancel_dates = user_data.get("cancel_dates", [])

    # 取得今天的隨機打卡時間（如果已產生）
    today_times = scheduled_times.get(uid, {})
    time_in    = today_times.get("in",      f"{PUNCH_IN_START[0]:02d}:{PUNCH_IN_START[1]:02d}~{PUNCH_IN_END[0]:02d}:{PUNCH_IN_END[1]:02d}")
    time_out   = today_times.get("out",     f"{PUNCH_OUT_START[0]:02d}:{PUNCH_OUT_START[1]:02d}~{PUNCH_OUT_END[0]:02d}:{PUNCH_OUT_END[1]:02d}")
    time_duty  = today_times.get("dutyout", f"{DUTY_OUT_START[0]:02d}:{DUTY_OUT_START[1]:02d}~{DUTY_OUT_END[0]:02d}:{DUTY_OUT_END[1]:02d}")

    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        date_str = d.strftime("%Y-%m-%d")
        weekday = weekday_names[d.weekday()]
        day_label = f"{month:02d}/{day:02d}（{weekday}）"
        prefix = "▶" if d == today else "　"
        is_past = d < today
        is_today = d == today

        # 昨天是否值班（影響今天是否打值班下班卡）
        prev_date = d - timedelta(days=1)
        prev_date_str = prev_date.strftime("%Y-%m-%d")
        has_duty_yesterday = prev_date_str in duty_days

        if date_str in leave_dates:
            # 休假日：顯示請假，但如果昨天值班還是要打下班卡
            if has_duty_yesterday:
                if is_today:
                    lines.append(f"{prefix}`{day_label}` 🏖️休假＋值班下班")
                    lines.append(f"　　　　　　⏰ 值班下班：{time_duty}")
                elif is_past:
                    lines.append(f"{prefix}`{day_label}` 🏖️休假＋值班下班 ✅")
                else:
                    lines.append(f"{prefix}`{day_label}` 🏖️休假＋值班下班")
                    lines.append(f"　　　　　　⏰ 值班下班：{DUTY_OUT_START[0]:02d}:{DUTY_OUT_START[1]:02d}~{DUTY_OUT_END[0]:02d}:{DUTY_OUT_END[1]:02d}")
            else:
                lines.append(f"{prefix}`{day_label}` 🏖️ 休假（不打卡）")

        elif date_str in cancel_dates:
            lines.append(f"{prefix}`{day_label}` ⏸️ 取消自動打卡（手動）")

        elif date_str in duty_days:
            # 值班日
            if is_today:
                lines.append(f"{prefix}`{day_label}` 🌙 值班")
                lines.append(f"　　　　　　⏰ 上班：{time_in}")
            elif is_past:
                lines.append(f"{prefix}`{day_label}` 🌙 值班 ✅")
            else:
                lines.append(f"{prefix}`{day_label}` 🌙 值班")
                lines.append(f"　　　　　　⏰ 上班：{PUNCH_IN_START[0]:02d}:{PUNCH_IN_START[1]:02d}~{PUNCH_IN_END[0]:02d}:{PUNCH_IN_END[1]:02d}")

        elif d.weekday() >= 5:
            # 週末
            if has_duty_yesterday:
                if is_today:
                    lines.append(f"{prefix}`{day_label}` 📴週末＋值班下班")
                    lines.append(f"　　　　　　⏰ 值班下班：{time_duty}")
                elif is_past:
                    lines.append(f"{prefix}`{day_label}` 📴週末＋值班下班 ✅")
                else:
                    lines.append(f"{prefix}`{day_label}` 📴週末＋值班下班")
                    lines.append(f"　　　　　　⏰ 值班下班：{DUTY_OUT_START[0]:02d}:{DUTY_OUT_START[1]:02d}~{DUTY_OUT_END[0]:02d}:{DUTY_OUT_END[1]:02d}")
            else:
                lines.append(f"{prefix}`{day_label}` 📴 週末")

        else:
            # 平日
            if has_duty_yesterday:
                # 值班隔天平日
                if is_today:
                    lines.append(f"{prefix}`{day_label}` 🟢 平日（值班隔天）")
                    lines.append(f"　　　　　　⏰ 值班下班：{time_duty}")
                elif is_past:
                    lines.append(f"{prefix}`{day_label}` 🟢 平日（值班隔天）✅")
                else:
                    lines.append(f"{prefix}`{day_label}` 🟢 平日（值班隔天）")
                    lines.append(f"　　　　　　⏰ 值班下班：{DUTY_OUT_START[0]:02d}:{DUTY_OUT_START[1]:02d}~{DUTY_OUT_END[0]:02d}:{DUTY_OUT_END[1]:02d}")
            else:
                # 一般平日
                if is_today:
                    lines.append(f"{prefix}`{day_label}` 🟢 平日（今天）")
                    lines.append(f"　　　　　　⏰ 上班：{time_in}　下班：{time_out}")
                elif is_past:
                    lines.append(f"{prefix}`{day_label}` 🟢 平日 ✅")
                else:
                    lines.append(f"{prefix}`{day_label}` 🟢 平日")
                    lines.append(f"　　　　　　⏰ 上班：{PUNCH_IN_START[0]:02d}:{PUNCH_IN_START[1]:02d}~{PUNCH_IN_END[0]:02d}:{PUNCH_IN_END[1]:02d}　下班：{PUNCH_OUT_START[0]:02d}:{PUNCH_OUT_START[1]:02d}~{PUNCH_OUT_END[0]:02d}:{PUNCH_OUT_END[1]:02d}")

    # 分兩個 embed 顯示（避免超過字數限制）
    mid = len(lines) // 2
    first_half = "\n".join(lines[:mid])
    second_half = "\n".join(lines[mid:])

    embed1 = discord.Embed(
        title=f"📆 {year}年{month}月 打卡狀態（上半月）",
        description=first_half,
        color=0x5865F2
    )
    embed2 = discord.Embed(
        title=f"📆 {year}年{month}月 打卡狀態（下半月）",
        description=second_half,
        color=0x5865F2
    )
    legend = "🟢平日　🌙值班　🏖️休假　⏸️取消自動　📴週末　✅已過"
    embed2.set_footer(text=legend)

    await interaction.response.send_message(embeds=[embed1, embed2], ephemeral=True)

_auto_punch_started = False  # 確保 auto_punch_task 只啟動一次
_commands_synced = False  # 每次程式啟動同步一次 slash command

@client.event
async def on_ready():
    global _auto_punch_started, _commands_synced
    # 每次程式啟動同步一次，確保新增/修改的 slash command 會出現在 Discord。
    if not _commands_synced:
        try:
            await tree.sync()
            _commands_synced = True
            print("✅ 指令同步完成")
        except Exception as e:
            print(f"⚠️ 指令同步失敗：{e}")
    else:
        print("✅ 指令已同步（跳過）")
    print(f"✅ Bot 已啟動：{client.user}")

    # auto_punch_task 只在第一次 on_ready 時啟動，斷線重連不重複啟動
    if not _auto_punch_started:
        _auto_punch_started = True
        client.loop.create_task(auto_punch_task(client))
        print("✅ auto_punch_task 已啟動")
    else:
        print("🔄 Discord 重新連線，auto_punch_task 繼續運行中")

@client.event
async def on_disconnect():
    print("⚠️ Discord 連線中斷，等待自動重連...")

# reconnect=True（預設）讓 discord.py 自動重連，log_handler=None 避免重複設定
client.run(DISCORD_TOKEN, reconnect=True, log_handler=None)
