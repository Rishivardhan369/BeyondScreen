"""Defensive ingestion helpers for user-supplied mobile analytics reports."""
import csv
import io
import logging
import re
from datetime import datetime
from pathlib import Path

try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None
    Image = None
    TESSERACT_AVAILABLE = False

logger = logging.getLogger(__name__)


def normalize_minutes(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    text = str(value).strip().lower()
    if not text:
        return None
    if text.startswith("-"):
        return None
    plain = re.fullmatch(r"(\d{1,4})\s*(?:minutes?|mins?)", text)
    if plain:
        return int(plain.group(1))
    hours = re.search(r"(\d{1,3})\s*(?:h|hr|hrs|hours?)\b", text)
    minutes = re.search(r"(\d{1,3})\s*(?:m|min|mins|minutes?)\b", text)
    if hours or minutes:
        return (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)
    return int(text) if re.fullmatch(r"\d{1,4}", text) else None


def detect_report_platform(text):
    lowered = text.lower()
    android = sum(term in lowered for term in ("digital wellbeing", "app timers", "unlocks", "dashboard"))
    ios = sum(term in lowered for term in ("daily average", "most used", "pickups", "screen time", "categories"))
    if android >= 2 and android > ios:
        return {"platform": "android", "confidence": "high" if android >= 3 else "moderate"}
    if ios >= 3 and ios > android:
        return {"platform": "ios", "confidence": "high" if ios >= 4 else "moderate"}
    if "screen time" in lowered or re.search(r"\d+\s*(?:h|hr).*\d+\s*(?:m|min)", lowered):
        return {"platform": "generic", "confidence": "limited"}
    return {"platform": "unknown", "confidence": "unknown"}


def _count(text, labels):
    for pattern in (rf"(?:{labels})\s*[:\-]?\s*(\d{{1,6}})\b", rf"(\d{{1,6}})\s*(?:{labels})\b"):
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    return None


def _duration(text, labels):
    match = re.search(rf"(?:{labels})\s*[:\-]?\s*((?:\d+\s*(?:h|hr|hrs|hours?))?\s*(?:\d+\s*(?:m|min|mins|minutes?))?)", text, re.I)
    return normalize_minutes(match.group(1)) if match and match.group(1).strip() else None


def _clock(text, labels):
    match = re.search(rf"(?:{labels})\s*[:\-]?\s*(\d{{1,2}}:\d{{2}}\s*(?:am|pm)?)", text, re.I)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1).strip()).upper()
    try:
        datetime.strptime(value, "%I:%M %p" if value.endswith(("AM", "PM")) else "%H:%M")
    except ValueError:
        return None
    return value


def _extract_total_screen_time(text):
    labels = r"total\s+screen\s+time|screen\s+time|daily\s+average"
    value = _duration(text, labels)
    if value is not None:
        return value
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if re.fullmatch(labels, line, re.I) and index + 1 < len(lines):
            value = normalize_minutes(lines[index + 1])
            if value is not None:
                return value
    return None


def _clean_name(value):
    return re.sub(r"\s+", " ", value).strip(" \t:|-")[:120]


def _extract_apps(text):
    apps = []
    reserved = re.compile(r"screen time|daily average|pickups?|unlocks?|notifications?|sessions?|most used|categories|longest session|dashboard|digital wellbeing|first use|first pickup|last use|latest use", re.I)
    pattern = re.compile(r"^(?P<name>[^|,:]{2,120}?)\s*(?:\||,|:|\s{2,})\s*(?P<duration>(?:\d+\s*(?:h|hr|hrs|hours?))?\s*(?:\d+\s*(?:m|min|mins|minutes?)))\s*$", re.I)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match and not reserved.search(match.group("name")):
            name, minutes = _clean_name(match.group("name")), normalize_minutes(match.group("duration"))
        elif index + 1 < len(lines) and not reserved.search(line) and len(line.split()) <= 8 and re.fullmatch(r"(?:\d+\s*(?:h|hr|hrs|hours?))?\s*(?:\d+\s*(?:m|min|mins|minutes?))", lines[index + 1], re.I):
            name, minutes = _clean_name(line), normalize_minutes(lines[index + 1])
        else:
            continue
        if name and minutes is not None:
            apps.append({"name": name, "minutes": minutes, "category": None})
    unique = {}
    for app in apps:
        unique.setdefault(app["name"].casefold(), app)
    return list(unique.values())[:50]


def normalize_mobile_analytics(payload):
    normalized = {
        "schema_version": 1,
        "source_type": payload.get("source_type") or "unknown",
        "platform": payload.get("platform") or "unknown",
        "detection_confidence": payload.get("detection_confidence") or "unknown",
    }
    for field in ("total_minutes", "pickups", "notifications", "sessions", "longest_session_minutes"):
        try:
            value = int(payload[field])
        except (KeyError, TypeError, ValueError):
            continue
        if value >= 0:
            normalized[field] = value
    for field in ("first_use_time", "last_use_time", "device", "report_date"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            normalized[field] = value.strip()[:120]
    apps = []
    for item in payload.get("apps", []) if isinstance(payload.get("apps", []), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            minutes = int(item.get("minutes"))
        except (TypeError, ValueError):
            continue
        name = _clean_name(str(item.get("name", "")))
        if name and minutes >= 0:
            category = item.get("category")
            apps.append({"name": name, "minutes": minutes, "category": _clean_name(str(category)) if category else None})
    normalized["apps"] = apps[:100]
    return normalized


def parse_mobile_analytics_text(text, source_hint=None):
    detected = detect_report_platform(text)
    source = source_hint
    if not source:
        source = {"android": "android_digital_wellbeing", "ios": "ios_screen_time"}.get(detected["platform"], "generic_ocr" if text.strip() else "unknown")
    return normalize_mobile_analytics({
        "source_type": source,
        "platform": detected["platform"],
        "detection_confidence": detected["confidence"],
        "total_minutes": _extract_total_screen_time(text),
        "apps": _extract_apps(text),
        "pickups": _count(text, r"pickups?|unlocks?"),
        "notifications": _count(text, r"notifications?"),
        "sessions": _count(text, r"sessions?"),
        "longest_session_minutes": _duration(text, r"longest\s+session"),
        "first_use_time": _clock(text, r"first\s+(?:pickup|use)"),
        "last_use_time": _clock(text, r"(?:last|latest)\s+(?:pickup|use)"),
    })


def _parse_csv(data):
    text = data.decode("utf-8-sig", errors="replace")
    rows, apps = list(csv.DictReader(io.StringIO(text))), []
    values = {"total_minutes": None, "pickups": None, "notifications": None, "sessions": None, "longest_session_minutes": None}
    for row in rows[:500]:
        fields = {str(key).strip().casefold(): value for key, value in row.items() if key}
        name = fields.get("app") or fields.get("app name") or fields.get("application")
        minutes = normalize_minutes(fields.get("minutes") or fields.get("duration") or fields.get("screen time"))
        if name and minutes is not None:
            apps.append({"name": name, "minutes": minutes, "category": fields.get("category") or None})
        values["total_minutes"] = values["total_minutes"] if values["total_minutes"] is not None else normalize_minutes(fields.get("total screen time"))
        for key in ("pickups", "notifications", "sessions"):
            if values[key] is None and str(fields.get(key, "")).strip().isdigit():
                values[key] = int(fields[key])
        values["longest_session_minutes"] = values["longest_session_minutes"] if values["longest_session_minutes"] is not None else normalize_minutes(fields.get("longest session"))
    if values["total_minutes"] is None and apps:
        values["total_minutes"] = sum(app["minutes"] for app in apps)
    detected = detect_report_platform(text)
    return normalize_mobile_analytics({**values, "source_type": "csv", "platform": detected["platform"], "detection_confidence": detected["confidence"], "apps": apps})


def parse_screen_time_report(uploaded_file):
    """Return structured metrics only; raw report content is never persisted."""
    suffix = Path(getattr(uploaded_file, "name", "")).suffix.lower()
    try:
        if suffix == ".csv":
            result = _parse_csv(uploaded_file.read())
        elif suffix == ".txt":
            result = parse_mobile_analytics_text(uploaded_file.read().decode("utf-8-sig", errors="replace"), "text")
        elif suffix == ".pdf":
            return None
        else:
            if not TESSERACT_AVAILABLE:
                return None
            image = Image.open(uploaded_file)
            image.verify()
            uploaded_file.seek(0)
            result = parse_mobile_analytics_text(pytesseract.image_to_string(Image.open(uploaded_file)))
        result["total_screen_time"] = result.get("total_minutes")
        return result if result.get("total_minutes") is not None or result.get("apps") or any(result.get(key) is not None for key in ("pickups", "notifications", "sessions")) else None
    except Exception as exc:
        logger.warning("Mobile analytics extraction failed (%s).", type(exc).__name__)
        return None


_parse_time_string = normalize_minutes
