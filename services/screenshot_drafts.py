"""Session-safe structured screenshot draft merging."""
from __future__ import annotations

import hashlib
import re
import uuid

MAX_SCREENSHOTS_PER_DRAFT = 5
MERGE_FIELDS = ("pickups", "unlocks", "notifications", "sessions", "longest_session_minutes", "first_use_time", "last_use_time")


def uploaded_digest(uploaded_file):
    uploaded_file.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: uploaded_file.read(64 * 1024), b""):
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def normalized_app_key(name):
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def create_draft(extraction, digest, *, filename, mood, goal, report_date):
    payload = dict(extraction)
    payload["apps"] = [dict(app, review_state="detected") for app in extraction.get("apps", [])]
    payload["report_date"] = payload.get("report_date") or report_date
    return {
        "token": uuid.uuid4().hex,
        "extraction": payload,
        "digests": [digest],
        "screenshot_count": 1,
        "filename": filename,
        "mood": mood,
        "goal": goal,
        "conflicts": [],
        "warnings": list(extraction.get("warnings", [])),
        "official_total_detected": extraction.get("total_minutes") is not None,
    }


def merge_into_draft(draft, incoming, digest):
    if digest in draft.get("digests", []):
        return draft, "duplicate"
    if draft.get("screenshot_count", 0) >= MAX_SCREENSHOTS_PER_DRAFT:
        return draft, "limit"
    merged = {**draft, "extraction": dict(draft["extraction"]), "digests": list(draft["digests"]),
              "conflicts": list(draft.get("conflicts", [])), "warnings": list(draft.get("warnings", []))}
    current = merged["extraction"]
    current_date, incoming_date = current.get("report_date"), incoming.get("report_date")
    if current_date and incoming_date and current_date != incoming_date:
        merged["conflicts"].append({"field": "report_date", "current": current_date, "incoming": incoming_date})
        merged["warnings"].append("The added screenshot appears to show a different report date and was not merged.")
        merged["digests"].append(digest)
        return merged, "date_conflict"

    incoming_total = incoming.get("total_minutes")
    if current.get("total_minutes") is None and incoming_total is not None:
        current["total_minutes"] = incoming_total
        current["total_screen_time"] = incoming_total
    elif incoming_total is not None and current.get("total_minutes") != incoming_total:
        merged["conflicts"].append({"field": "total_minutes", "current": current.get("total_minutes"), "incoming": incoming_total})

    for field in MERGE_FIELDS:
        incoming_value = incoming.get(field)
        if current.get(field) is None and incoming_value is not None:
            current[field] = incoming_value
        elif incoming_value is not None and current.get(field) != incoming_value:
            merged["conflicts"].append({"field": field, "current": current.get(field), "incoming": incoming_value})

    apps = [dict(app) for app in current.get("apps", [])]
    by_key = {normalized_app_key(app.get("name")): app for app in apps}
    for incoming_app in incoming.get("apps", []):
        key = normalized_app_key(incoming_app.get("name"))
        if not key:
            continue
        existing = by_key.get(key)
        if existing is None:
            app = dict(incoming_app, review_state="detected")
            apps.append(app)
            by_key[key] = app
        elif existing.get("minutes") != incoming_app.get("minutes"):
            existing["review_state"] = "conflict"
            existing["conflicting_minutes"] = incoming_app.get("minutes")
            merged["conflicts"].append({"field": "app", "app": existing.get("name"), "current": existing.get("minutes"), "incoming": incoming_app.get("minutes")})
    current["apps"] = apps
    current["recognized_app_total_minutes"] = sum(app.get("minutes", 0) for app in apps)
    merged["official_total_detected"] = bool(merged.get("official_total_detected") or incoming_total is not None)
    merged["digests"].append(digest)
    merged["screenshot_count"] = merged.get("screenshot_count", 0) + 1
    merged["warnings"].extend(w for w in incoming.get("warnings", []) if w not in merged["warnings"])
    return merged, "merged"


def coverage_summary(total, apps):
    recognized = sum(app.get("minutes", 0) for app in apps if isinstance(app, dict) and isinstance(app.get("minutes"), int))
    ratio = round(recognized / total * 100, 1) if total and total > 0 else None
    return {"official_total_minutes": total, "recognized_app_minutes": recognized, "ratio": ratio,
            "exceeds_total": total is not None and recognized > total}
