"""Defensive parsing for user-supplied mobile analytics reports."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from .screenshot_ingestion import extract_screenshot

MAX_DAILY_MINUTES = 1440


def normalize_ocr_text(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.translate(str.maketrans({"：": ":", "–": "-", "—": "-", "•": " ", "·": " "}))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(re.sub(r"[\t \u00a0]+", " ", line).strip() for line in text.split("\n") if line.strip())


def _duration_context(value):
    return re.sub(
        r"(?i)\b([0-9oOilI]{1,4})(?=\s*(?:h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b)",
        lambda match: match.group(1).translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "i": "1"})),
        value,
    )


def normalize_minutes(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number if 0 <= number <= MAX_DAILY_MINUTES else None
    text = _duration_context(normalize_ocr_text(value).casefold()).strip().rstrip(".")
    if not text or text.startswith("-"):
        return None
    text = re.sub(r"(?<=\d)(?=[hm])|(?<=[hm])(?=\d)", " ", text)
    if re.fullmatch(r"\d{1,4}", text):
        number = int(text)
        return number if number <= MAX_DAILY_MINUTES else None
    hours = re.search(r"\b(\d{1,3})\s*(?:h|hr|hrs|hour|hours)\.?\b", text)
    minutes = re.search(r"\b(\d{1,4})\s*(?:m|min|mins|minute|minutes)\.?\b", text)
    if not hours and not minutes:
        return None
    total = (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)
    return total if 0 <= total <= MAX_DAILY_MINUTES else None


def detect_screenshot_provider(text):
    lowered = normalize_ocr_text(text).casefold()
    groups = {
        "SAMSUNG_DIGITAL_WELLBEING": {"digital wellbeing and parental controls": 5, "device care": 4, "app timers": 2, "most used apps": 2, "screen time": 1},
        "ANDROID_DIGITAL_WELLBEING": {"digital wellbeing": 4, "ways to disconnect": 4, "focus mode": 3, "dashboard": 2, "unlocks": 2, "app timers": 2, "screen time": 1},
        "IOS_SCREEN_TIME": {"see all app & website activity": 5, "app & website activity": 4, "daily average": 3, "most used": 2, "pickups": 2, "categories": 2, "screen time": 1},
        "GENERIC_USAGE_ANALYTICS": {"screen time": 2, "app usage": 2, "usage": 1},
    }
    scores = {provider: sum(weight for term, weight in terms.items() if term in lowered) for provider, terms in groups.items()}
    provider, score = max(scores.items(), key=lambda item: item[1])
    if score < 2:
        return {"provider": "UNKNOWN", "platform": "unknown", "confidence": "NONE", "score": score}
    ordered = sorted(scores.values(), reverse=True)
    if len(ordered) > 1 and score == ordered[1] and provider != "GENERIC_USAGE_ANALYTICS":
        provider = "GENERIC_USAGE_ANALYTICS"
    confidence = "HIGH" if score >= 7 else "MEDIUM" if score >= 4 else "LOW"
    platform = "ios" if provider == "IOS_SCREEN_TIME" else "android" if provider in ("ANDROID_DIGITAL_WELLBEING", "SAMSUNG_DIGITAL_WELLBEING") else "generic"
    return {"provider": provider, "platform": platform, "confidence": confidence, "score": score}


def detect_report_platform(text):
    result = detect_screenshot_provider(text)
    return {"platform": result["platform"], "confidence": result["confidence"].lower()}


def _count(text, label):
    for pattern in (rf"(?:{label})\s*[:\-]?\s*(\d{{1,6}})\b", rf"\b(\d{{1,6}})\s*(?:{label})\b"):
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    return None


DURATION_FRAGMENT = r"(?:[0-9oOilI]{1,3}\s*(?:h|hr|hrs|hour|hours)\.?\s*)?(?:[0-9oOilI]{1,4}\s*(?:m|min|mins|minute|minutes)\.?)?"
RESERVED = re.compile(r"screen time|daily average|pickups?|unlocks?|notifications?|sessions?|most used|categories|longest session|dashboard|digital wellbeing|device care|first use|first pickup|last use|latest use|today|yesterday", re.I)


def _duration_for_label(text, labels):
    match = re.search(rf"(?:{labels})\s*[:\-]?\s*([^\n]{{1,40}})", text, re.I)
    return normalize_minutes(match.group(1)) if match and match.group(1).strip() else None


def _extract_total(text):
    labels = r"total\s+screen\s+time|screen\s+time|daily\s+average"
    value = _duration_for_label(text, labels)
    if value is not None:
        return value
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if re.fullmatch(labels, line, re.I):
            value = normalize_minutes(lines[index + 1])
            if value is not None:
                return value
    return None


def _clean_name(value):
    return re.sub(r"\s+", " ", value).strip(" :|,-")[:120]


def _is_app_name(line):
    return bool(2 <= len(line) <= 120 and re.search(r"[A-Za-z]", line) and not RESERVED.search(line) and normalize_minutes(line) is None and len(line.split()) <= 10)


def _extract_apps(text):
    lines, found = [line for line in text.splitlines() if line], []
    duration_re = re.compile(rf"(?P<duration>{DURATION_FRAGMENT})\s*$", re.I)
    for index, line in enumerate(lines):
        match = duration_re.search(line)
        minutes = normalize_minutes(match.group("duration")) if match and match.group("duration").strip() else None
        name = _clean_name(line[:match.start()]) if minutes is not None else ""
        if minutes is not None and _is_app_name(name):
            found.append({"name": name, "minutes": minutes, "category": None})
        elif _is_app_name(line) and index + 1 < len(lines) and normalize_minutes(lines[index + 1]) is not None:
            found.append({"name": _clean_name(line), "minutes": normalize_minutes(lines[index + 1]), "category": None})
        elif minutes is not None and not name and index > 0 and _is_app_name(lines[index - 1]):
            found.append({"name": _clean_name(lines[index - 1]), "minutes": minutes, "category": None})
    unique = {}
    for app in found:
        unique.setdefault(app["name"].casefold(), app)
    return list(unique.values())[:50]


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


def parse_screenshot_text(text):
    text = normalize_ocr_text(text)
    detected, apps, total = detect_screenshot_provider(text), _extract_apps(text), _extract_total(text)
    pickups, unlocks = _count(text, r"pickups?"), _count(text, r"unlocks?")
    structured_count = int(total is not None) + len(apps) + int(pickups is not None) + int(unlocks is not None)
    confidence = detected["confidence"]
    if structured_count and confidence == "NONE":
        confidence = "LOW"
    elif structured_count >= 3 and confidence in ("LOW", "MEDIUM"):
        confidence = "MEDIUM"
    warnings = []
    if total is None and apps:
        warnings.append("Official total was not detected; the app total is shown separately.")
    if structured_count and confidence == "LOW":
        warnings.append("Automatic extraction was partial. Review every detected value before saving.")
    return {"provider": detected["provider"], "confidence": confidence, "total_minutes": total, "apps": apps,
            "pickups": pickups, "unlocks": unlocks, "notifications": _count(text, r"notifications?"),
            "sessions": _count(text, r"sessions?"), "longest_session_minutes": _duration_for_label(text, r"longest\s+session"),
            "first_use_time": _clock(text, r"first\s+(?:pickup|use)"), "last_use_time": _clock(text, r"(?:last|latest)\s+(?:pickup|use)"),
            "has_analytics": bool(structured_count), "warnings": warnings}


def normalize_mobile_analytics(payload):
    normalized = {"schema_version": 1, "source_type": payload.get("source_type") or "unknown", "platform": payload.get("platform") or "unknown", "detection_confidence": payload.get("detection_confidence") or "unknown", "apps": []}
    for field in ("total_minutes", "pickups", "unlocks", "notifications", "sessions", "longest_session_minutes"):
        try:
            value = int(payload[field])
        except (KeyError, TypeError, ValueError):
            continue
        maximum = MAX_DAILY_MINUTES if field in ("total_minutes", "longest_session_minutes") else 1_000_000
        if 0 <= value <= maximum:
            normalized[field] = value
    for field in ("first_use_time", "last_use_time", "device", "report_date"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            normalized[field] = value.strip()[:120]
    for item in payload.get("apps", []) if isinstance(payload.get("apps"), list) else []:
        if not isinstance(item, dict):
            continue
        minutes, name = normalize_minutes(item.get("minutes")), _clean_name(str(item.get("name", "")))
        if name and minutes is not None:
            normalized["apps"].append({"name": name, "minutes": minutes, "category": item.get("category") or None})
    return normalized


def parse_mobile_analytics_text(text, source_hint=None):
    result, detected = parse_screenshot_text(text), detect_screenshot_provider(text)
    return normalize_mobile_analytics({**result, "source_type": source_hint or {"android": "android_digital_wellbeing", "ios": "ios_screen_time"}.get(detected["platform"], "generic_ocr"), "platform": detected["platform"], "detection_confidence": result["confidence"].lower()})


def _parse_csv(data):
    rows, apps, values = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig", errors="replace")))), [], {}
    for row in rows[:500]:
        fields = {str(key).strip().casefold(): value for key, value in row.items() if key}
        name = fields.get("app") or fields.get("app name") or fields.get("application")
        minutes = normalize_minutes(fields.get("minutes") or fields.get("duration") or fields.get("screen time"))
        if name and minutes is not None:
            apps.append({"name": name, "minutes": minutes, "category": fields.get("category") or None})
        total = normalize_minutes(fields.get("total screen time"))
        if total is not None and "total_minutes" not in values:
            values["total_minutes"] = total
        for key in ("pickups", "unlocks", "notifications", "sessions"):
            if key not in values and str(fields.get(key, "")).strip().isdigit():
                values[key] = int(fields[key])
    result = normalize_mobile_analytics({**values, "source_type": "csv", "platform": "generic", "apps": apps})
    result["recognized_app_total_minutes"] = sum(app["minutes"] for app in result["apps"])
    return result


def parse_screen_time_report(uploaded_file):
    """Return typed public metadata and normalized fields; never raw OCR text."""
    suffix = Path(getattr(uploaded_file, "name", "")).suffix.casefold()
    if suffix == ".csv":
        result = _parse_csv(uploaded_file.read())
        result.update(status="SUCCESS" if result["apps"] or result.get("total_minutes") is not None else "NO_ANALYTICS_FOUND", confidence="HIGH")
        result["total_screen_time"] = result.get("total_minutes")
        return result
    if suffix == ".txt":
        result = parse_mobile_analytics_text(uploaded_file.read().decode("utf-8-sig", errors="replace"), "text")
        result.update(status="SUCCESS" if result["apps"] or result.get("total_minutes") is not None else "NO_ANALYTICS_FOUND", confidence="MEDIUM")
        result["total_screen_time"] = result.get("total_minutes")
        return result
    if suffix == ".pdf":
        return {"status": "UNSUPPORTED_REPORT", "apps": []}
    return extract_screenshot(uploaded_file, parse_screenshot_text).to_payload()


_parse_time_string = normalize_minutes
