import re
from datetime import date
from html import unescape


NO_RECORD_MESSAGE = "今日尚無刷卡記錄"


def _cell_text(fragment):
    text = re.sub(r"<[^>]+>", "", fragment)
    return unescape(text).replace("&nbsp;", "").replace("\xa0", "").strip()


def _format_hhmm(value):
    value = value.strip()
    if not re.fullmatch(r"\d{4}", value):
        return None
    hour, minute = int(value[:2]), int(value[2:])
    if hour > 23 or minute > 59:
        return None
    return f"{value[:2]}:{value[2:]}"


def parse_today_punch_html(html, empid, target_date=None):
    """Parse one employee's row from the e-HR attendance summary HTML."""
    target_date = target_date or date.today()
    roc_year = target_date.year - 1911
    date_pattern = re.compile(
        rf"^{roc_year}/0?{target_date.month}/0?{target_date.day}(?:\D|$)"
    )

    html = unescape(html)
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", html, re.IGNORECASE | re.DOTALL)
    matching_rows = []

    for row in rows:
        cells = [
            _cell_text(cell)
            for cell in re.findall(
                r"<td\b[^>]*>(.*?)</td\s*>", row, re.IGNORECASE | re.DOTALL
            )
        ]
        date_index = next(
            (index for index, value in enumerate(cells) if date_pattern.search(value)),
            None,
        )
        if date_index is None:
            continue
        matching_rows.append((cells, date_index))

    if not matching_rows:
        return {"success": False, "message": NO_RECORD_MESSAGE}

    # If the response contains more than one employee, prefer the exact employee row.
    cells, date_index = next(
        (
            item
            for item in matching_rows
            if str(empid).strip() in {value.strip() for value in item[0][: item[1]]}
        ),
        matching_rows[0],
    )

    raw_in = cells[date_index + 2] if len(cells) > date_index + 2 else ""
    raw_out = cells[date_index + 3] if len(cells) > date_index + 3 else ""
    clock_in = _format_hhmm(raw_in)
    clock_out = _format_hhmm(raw_out)

    # e-HR has changed/inserted columns before. Scan only cells after the date for
    # valid HHMM tokens so a real punch remains detectable even if column offsets move.
    all_times = set()
    for cell in cells[date_index + 1 :]:
        for match in re.finditer(r"(?<!\d)([01]\d|2[0-3])([0-5]\d)(?!\d)", cell):
            all_times.add(f"{match.group(1)}:{match.group(2)}")

    if not all_times and not clock_in and not clock_out:
        return {"success": False, "message": NO_RECORD_MESSAGE}

    return {
        "success": True,
        "times": sorted(all_times),
        "clock_in": clock_in,
        "clock_out": clock_out,
    }
