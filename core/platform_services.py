"""Security-conscious services for timezones, devices, reminders and long-term reports."""
from __future__ import annotations

import hashlib
import secrets
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import (
    DevicePairingCode, DigitalSummary, EmailVerification, InAppNotification,
    MaintenanceJobRun, Reminder, ScreenTimeTarget, SecurityEvent, UserDevice,
)

CONSENT_VERSION = "2026-08"
DEVICE_SCHEMA_VERSION = 1
PAIRING_TTL_MINUTES = 10


def normalize_app_key(value):
    return " ".join(str(value or "").strip().casefold().split())[:160]


def valid_timezone(value):
    try:
        ZoneInfo(value)
        return True
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False


def user_zone(user):
    name = getattr(getattr(user, "userprofile", None), "timezone", settings.TIME_ZONE)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(settings.TIME_ZONE)


def local_date(user, value=None):
    value = value or timezone.now()
    return value.astimezone(user_zone(user)).date()


def month_bounds(month_value=None, *, today=None):
    today = today or timezone.localdate()
    try:
        start = datetime.strptime(month_value, "%Y-%m").date().replace(day=1) if month_value else today.replace(day=1)
    except (TypeError, ValueError):
        start = today.replace(day=1)
    if start > today.replace(day=1):
        start = today.replace(day=1)
    following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, following - timedelta(days=1)


def hash_secret(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def throttle(key, *, limit, seconds):
    count = cache.get(key, 0)
    if count >= limit:
        return False
    cache.set(key, count + 1, seconds)
    return True


def issue_email_verification(user, purpose=EmailVerification.PURPOSE_VERIFY, email=None):
    raw = secrets.token_urlsafe(32)
    verification = EmailVerification.objects.create(
        user=user, purpose=purpose, email=email or user.email,
        token_hash=hash_secret(raw), expires_at=timezone.now() + timedelta(hours=24),
    )
    path = reverse("core:verify_email", args=[raw])
    send_mail("Verify your BeyondScreen email", f"Open this link within 24 hours: {path}", settings.DEFAULT_FROM_EMAIL, [verification.email], fail_silently=True)
    return raw


def create_pairing_code(user):
    raw = "-".join((secrets.token_hex(2), secrets.token_hex(2))).upper()
    DevicePairingCode.objects.create(
        user=user, code_hash=hash_secret(raw), expires_at=timezone.now() + timedelta(minutes=PAIRING_TTL_MINUTES),
        consent_version=CONSENT_VERSION,
    )
    return raw


def issue_device_token():
    raw = secrets.token_urlsafe(40)
    return raw, hash_secret(raw)


def authenticate_device(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    digest = hash_secret(header[7:].strip())
    return UserDevice.objects.filter(token_hash=digest, is_active=True, revoked_at__isnull=True).select_related("user", "user__userprofile").first()


def in_quiet_hours(profile, moment=None):
    if not profile.quiet_hours_start or not profile.quiet_hours_end:
        return False
    local = (moment or timezone.now()).astimezone(user_zone(profile.user)).time().replace(tzinfo=None)
    start, end = profile.quiet_hours_start, profile.quiet_hours_end
    return start <= local < end if start < end else local >= start or local < end


def dispatch_due_reminders(now=None):
    now = now or timezone.now()
    processed = 0
    for reminder in Reminder.objects.filter(enabled=True, due_at__lte=now).select_related("user", "user__userprofile")[:500]:
        profile = reminder.user.userprofile
        if not profile.reminders_enabled or in_quiet_hours(profile, now):
            continue
        period = reminder.due_at.astimezone(user_zone(reminder.user)).strftime("%Y%m%d%H")
        key = f"reminder:{reminder.pk}:{period}"
        if profile.in_app_reminders:
            _, created = InAppNotification.objects.get_or_create(
                user=reminder.user, dedupe_key=key,
                defaults={"notification_type": reminder.reminder_type, "title": reminder.title, "message": reminder.message, "link": reminder.link},
            )
            processed += int(created)
        if profile.email_reminders:
            send_mail(reminder.title, reminder.message, settings.DEFAULT_FROM_EMAIL, [reminder.user.email], fail_silently=True)
        reminder.last_dispatched_at = now
        reminder.enabled = False
        reminder.save(update_fields=["last_dispatched_at", "enabled"])
    MaintenanceJobRun.objects.update_or_create(job_name="process_reminders", defaults={"last_run_at": now, "last_success_at": now, "processed_count": processed, "status": "success", "error_code": ""})
    return processed


def build_monthly_review(user, start, end):
    summaries = list(DigitalSummary.objects.filter(user=user, created_at__date__range=(start, end)).only("created_at", "screen_time_minutes", "mobile_analytics_snapshot", "mobile_assessment_snapshot"))
    apps, categories, inputs, sources, qualities = Counter(), Counter(), Counter(), Counter(), Counter()
    pickups, notifications = [], []
    total = 0
    for summary in summaries:
        total += summary.screen_time_minutes
        mobile = summary.mobile_analytics_snapshot or {}
        sources[mobile.get("source_type", "unknown")] += 1
        qualities[(summary.mobile_assessment_snapshot or {}).get("data_quality", "Legacy/unavailable")] += 1
        if mobile.get("pickups") is not None: pickups.append(mobile["pickups"])
        if mobile.get("notifications") is not None: notifications.append(mobile["notifications"])
        for app in mobile.get("apps", []):
            apps[app.get("name", "Unknown")] += app.get("minutes", 0)
            categories[app.get("category") or "Unknown"] += app.get("minutes", 0)
        for item in (summary.mobile_assessment_snapshot or {}).get("actionable_inputs", []): inputs[item.get("type", "unknown")] += 1
    entries = user.momentum_entries.filter(completed_at__date__range=(start, end)).select_related("goal")
    outcomes = user.goal_rescue_outcomes.filter(shown_at__date__range=(start, end))
    reclaimed = sum(item.duration_minutes for item in entries)
    return {
        "start": start, "end": end, "report_count": len(summaries), "recorded_total": total,
        "recorded_average": round(total / len(summaries), 1) if summaries else None,
        "top_apps": apps.most_common(5), "top_categories": categories.most_common(5),
        "average_pickups": round(sum(pickups) / len(pickups), 1) if pickups else None,
        "average_notifications": round(sum(notifications) / len(notifications), 1) if notifications else None,
        "momentum_count": entries.count(), "reclaimed_minutes": reclaimed,
        "rescue_completed": outcomes.filter(status="completed").count(), "rescue_skipped": outcomes.filter(status="skipped").count(),
        "input_types": inputs.most_common(), "sources": dict(sources), "qualities": dict(qualities),
    }


def record_security_event(user, event_type, **metadata):
    return SecurityEvent.objects.create(user=user, event_type=event_type, metadata=metadata)
