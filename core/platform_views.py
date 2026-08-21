"""User-facing completion features and the versioned companion-device API."""
import csv
import io
import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.db import IntegrityError, connection, transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from services.screen_time_parser import normalize_mobile_analytics
from .forms import ReminderForm, ScreenTimeTargetForm, UserAppPreferenceForm
from .mobile_analytics import build_mobile_analytics_assessment
from .models import (
    DeviceAnalyticsReport, DevicePairingCode, DigitalSummary, EmailVerification,
    InAppNotification, MomentumEntry, Postcard, Reminder, ScreenTimeTarget,
    SecurityEvent, UserAppPreference, UserDevice, UserGoal,
)
from .platform_services import (
    CONSENT_VERSION, DEVICE_SCHEMA_VERSION, authenticate_device, build_monthly_review,
    create_pairing_code, hash_secret, issue_device_token, issue_email_verification,
    local_date, month_bounds, normalize_app_key, record_security_event, throttle,
)
from .services import build_goal_rescue, ensure_goal_rescue_outcome, freeze_goal_rescue_snapshot


def _json_body(request, maximum=256 * 1024):
    if int(request.META.get("CONTENT_LENGTH") or 0) > maximum:
        raise ValueError("payload_too_large")
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("invalid_json")


def _safe_csv(value):
    text = str(value if value is not None else "")
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


@require_GET
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return JsonResponse({"status": "ready"})
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)


@login_required
def app_preferences(request):
    names = {}
    for summary in request.user.digital_summaries.only("mobile_analytics_snapshot"):
        for app in (summary.mobile_analytics_snapshot or {}).get("apps", []):
            key = normalize_app_key(app.get("name"))
            if key: names.setdefault(key, app.get("name"))
    preferences = {item.normalized_app_name: item for item in request.user.app_preferences.select_related("linked_goal")}
    query = request.GET.get("q", "").strip().casefold()
    rows = [{"key": key, "name": name, "preference": preferences.get(key)} for key, name in names.items() if not query or query in name.casefold()]
    return render(request, "platform/app_preferences.html", {"apps": sorted(rows, key=lambda row: row["name"].casefold()), "query": query})


@login_required
def app_preference_edit(request, app_key):
    key = normalize_app_key(app_key)
    preference = UserAppPreference.objects.filter(user=request.user, normalized_app_name=key).first()
    initial = {"display_name": request.GET.get("name", app_key)}
    form = UserAppPreferenceForm(request.POST or None, instance=preference, initial=initial, user=request.user)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False); item.user = request.user; item.normalized_app_name = key; item.save()
        messages.success(request, "App preference saved. New assessments will use this override.")
        return redirect("core:app_preferences")
    return render(request, "platform/form.html", {"form": form, "title": "App preference", "subtitle": "Historical assessments stay frozen.", "cancel_url": reverse("core:app_preferences")})


@login_required
@require_POST
def app_preference_reset(request, app_key):
    UserAppPreference.objects.filter(user=request.user, normalized_app_name=normalize_app_key(app_key)).delete()
    messages.success(request, "App override reset to automatic/unknown.")
    return redirect("core:app_preferences")


@login_required
def app_detail(request, app_key):
    key = normalize_app_key(app_key)
    rows = []
    for summary in request.user.digital_summaries.only("id", "created_at", "screen_time_minutes", "mobile_analytics_snapshot"):
        for app in (summary.mobile_analytics_snapshot or {}).get("apps", []):
            if normalize_app_key(app.get("name")) == key:
                rows.append({"summary": summary, "minutes": app.get("minutes", 0), "share": round(min(100, app.get("minutes", 0) / summary.screen_time_minutes * 100), 1) if summary.screen_time_minutes else 0})
    if not rows: raise Http404
    preference = UserAppPreference.objects.filter(user=request.user, normalized_app_name=key).select_related("linked_goal").first()
    return render(request, "platform/app_detail.html", {"app_name": rows[0]["summary"].mobile_analytics_snapshot.get("apps", [{}])[0].get("name", app_key), "rows": rows, "total": sum(row["minutes"] for row in rows), "average": round(sum(row["minutes"] for row in rows) / len(rows), 1), "preference": preference})


@login_required
def targets(request):
    form = ScreenTimeTargetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        target = form.save(commit=False); target.user = request.user
        target.save(); messages.success(request, "Screen-time target saved."); return redirect("core:targets")
    return render(request, "platform/targets.html", {"form": form, "targets": request.user.screen_time_targets.all()})


@login_required
@require_POST
def target_delete(request, target_id):
    get_object_or_404(ScreenTimeTarget, pk=target_id, user=request.user).delete()
    return redirect("core:targets")


@login_required
def notifications(request):
    return render(request, "platform/notifications.html", {"notifications": request.user.notifications.all()[:100], "reminders": request.user.reminders.filter(enabled=True)})


@login_required
def reminder_create(request):
    form = ReminderForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        reminder = form.save(commit=False); reminder.user = request.user; reminder.save()
        return redirect("core:notifications")
    return render(request, "platform/form.html", {"form": form, "title": "New reminder", "subtitle": "You control when BeyondScreen reminds you.", "cancel_url": reverse("core:notifications")})


@login_required
@require_POST
def notification_read(request, notification_id):
    item = get_object_or_404(InAppNotification, pk=notification_id, user=request.user)
    item.read_at = timezone.now(); item.save(update_fields=["read_at"])
    return redirect(item.link if item.link.startswith("/") else "core:notifications")


@login_required
@require_POST
def notifications_read_all(request):
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect("core:notifications")


@login_required
def privacy_center(request):
    return render(request, "platform/privacy_center.html")


def privacy_policy(request): return render(request, "platform/privacy_policy.html")
def terms(request): return render(request, "platform/terms.html")


@login_required
@require_POST
def delete_summary(request, summary_id):
    summary = get_object_or_404(DigitalSummary, pk=summary_id, user=request.user)
    summary.delete(); messages.success(request, "The report and its directly related assessment, Rescue outcome, feedback, and Momentum were deleted.")
    return redirect("core:history")


@login_required
def delete_summary_confirm(request, summary_id):
    summary = get_object_or_404(DigitalSummary, pk=summary_id, user=request.user)
    return render(request, "platform/delete_summary.html", {"summary": summary})


@login_required
def delete_account(request):
    if request.method == "POST":
        if not check_password(request.POST.get("password", ""), request.user.password) or request.POST.get("confirm") != "DELETE":
            messages.error(request, "Enter your password and type DELETE to confirm.")
        else:
            user = request.user
            record_security_event(user, "account_deletion_initiated")
            logout(request); user.delete()
            return redirect("core:home")
    return render(request, "platform/delete_account.html")


@login_required
def sessions(request):
    current = request.session.session_key
    items = []
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        try:
            if str(session.get_decoded().get("_auth_user_id")) == str(request.user.pk): items.append({"current": session.session_key == current, "expires": session.expire_date})
        except Exception: continue
    return render(request, "platform/sessions.html", {"sessions": items, "events": request.user.security_events.all()[:20]})


@login_required
@require_POST
def logout_other_sessions(request):
    current = request.session.session_key
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        try:
            if session.session_key != current and str(session.get_decoded().get("_auth_user_id")) == str(request.user.pk): session.delete()
        except Exception: continue
    record_security_event(request.user, "other_sessions_logged_out")
    messages.success(request, "Other active sessions were signed out.")
    return redirect("core:sessions")


@login_required
def email_verification_resend(request):
    if request.method == "POST" and throttle(f"verify:{request.user.pk}", limit=3, seconds=3600):
        issue_email_verification(request.user); messages.success(request, "If delivery is configured, a new verification link was sent.")
    elif request.method == "POST": messages.warning(request, "Please wait before requesting another verification message.")
    return redirect("core:profile")


@login_required
def email_change(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        if "@" not in email or len(email) > 254:
            messages.error(request, "Enter a valid email address.")
        elif User.objects.filter(email__iexact=email).exclude(pk=request.user.pk).exists():
            messages.success(request, "If that address can be used, a verification message will be sent.")
        elif not throttle(f"email-change:{request.user.pk}", limit=3, seconds=3600):
            messages.warning(request, "Please wait before requesting another email change.")
        else:
            request.user.userprofile.pending_email = email
            request.user.userprofile.save(update_fields=["pending_email"])
            issue_email_verification(request.user, EmailVerification.PURPOSE_CHANGE, email)
            messages.success(request, "Check the new address. Your current email remains active until verification.")
            return redirect("core:profile")
    return render(request, "platform/email_change.html")


def verify_email(request, token):
    verification = EmailVerification.objects.filter(token_hash=hash_secret(token)).select_related("user", "user__userprofile").first()
    state = "invalid"
    if verification and verification.consumed_at: state = "already_verified"
    elif verification and verification.expires_at >= timezone.now():
        with transaction.atomic():
            verification.consumed_at = timezone.now(); verification.save(update_fields=["consumed_at"])
            if verification.purpose == EmailVerification.PURPOSE_CHANGE:
                verification.user.email = verification.email; verification.user.save(update_fields=["email"]); verification.user.userprofile.pending_email = ""
            verification.user.userprofile.email_verified_at = timezone.now(); verification.user.userprofile.save(update_fields=["pending_email", "email_verified_at"])
            from allauth.account.models import EmailAddress
            EmailAddress.objects.update_or_create(
                user=verification.user,
                email=verification.user.email,
                defaults={"verified": True, "primary": True},
            )
        state = "verified"
    elif verification: state = "expired"
    return render(request, "platform/email_verification.html", {"state": state})


@login_required
def monthly_review(request):
    start, end = month_bounds(request.GET.get("month"), today=local_date(request.user))
    return render(request, "platform/monthly_review.html", {"review": build_monthly_review(request.user, start, end), "selected_month": start.strftime("%Y-%m"), "previous_month": (start - timedelta(days=1)).strftime("%Y-%m"), "next_month": (end + timedelta(days=1)).strftime("%Y-%m")})


@login_required
def monthly_review_csv(request):
    start, end = month_bounds(request.GET.get("month"), today=local_date(request.user)); review = build_monthly_review(request.user, start, end)
    response = HttpResponse(content_type="text/csv; charset=utf-8"); response["Content-Disposition"] = f'attachment; filename="beyondscreen-monthly-{start:%Y-%m}.csv"'
    writer = csv.writer(response); writer.writerow(["Metric", "Value"])
    for key in ("recorded_total", "recorded_average", "report_count", "reclaimed_minutes", "momentum_count", "rescue_completed", "rescue_skipped"): writer.writerow([key, _safe_csv(review[key])])
    for app, minutes in review["top_apps"]: writer.writerow(["app", _safe_csv(app), minutes])
    return response


@login_required
def monthly_review_pdf(request):
    start, end = month_bounds(request.GET.get("month"), today=local_date(request.user)); review = build_monthly_review(request.user, start, end)
    output = io.BytesIO(); pdf = canvas.Canvas(output, pagesize=letter); y = 750
    pdf.setTitle("BeyondScreen Monthly Review"); pdf.drawString(54, y, "BeyondScreen — Monthly Review"); y -= 30
    for line in (f"{start:%B %Y}", f"Reports: {review['report_count']}", f"Recorded total: {review['recorded_total']} minutes", f"Recorded average: {review['recorded_average'] or 'Unavailable'}", f"Reclaimed time: {review['reclaimed_minutes']} minutes", f"Momentum completions: {review['momentum_count']}"):
        pdf.drawString(54, y, line[:95]); y -= 20
    pdf.save(); response = HttpResponse(output.getvalue(), content_type="application/pdf"); response["Content-Disposition"] = f'attachment; filename="beyondscreen-monthly-{start:%Y-%m}.pdf"'; return response


@login_required
def search(request):
    q = request.GET.get("q", "").strip()[:100]
    goals = request.user.goals.filter(title__icontains=q)[:10] if q else []
    actions = request.user.goals.filter(actions__title__icontains=q).distinct()[:10] if q else []
    reports = request.user.digital_summaries.filter(created_at__date=q)[:10] if q and len(q) == 10 else []
    apps = []
    if q:
        seen = set()
        for summary in request.user.digital_summaries.only("mobile_analytics_snapshot"):
            for app in (summary.mobile_analytics_snapshot or {}).get("apps", []):
                if q.casefold() in app.get("name", "").casefold() and app.get("name") not in seen: apps.append(app); seen.add(app.get("name"))
    postcards = request.user.postcards.filter(reflection__icontains=q)[:10] if q else []
    return render(request, "platform/search.html", {"query": q, "goals": goals, "action_goals": actions, "reports": reports, "apps": apps[:10], "postcards": postcards})


@login_required
def devices(request):
    return render(request, "platform/devices.html", {"devices": request.user.devices.all(), "pairing_code": request.session.pop("pairing_code", None), "consent_version": CONSENT_VERSION})


@login_required
@require_POST
def device_pairing_create(request):
    if request.POST.get("consent") != "yes": messages.error(request, "Explicit device analytics consent is required.")
    else: request.session["pairing_code"] = create_pairing_code(request.user)
    return redirect("core:devices")


@login_required
@require_POST
def device_revoke(request, device_id):
    device = get_object_or_404(UserDevice, pk=device_id, user=request.user); device.is_active = False; device.revoked_at = timezone.now(); device.save(update_fields=["is_active", "revoked_at"]); record_security_event(request.user, "device_revoked", device=str(device.public_id)); return redirect("core:devices")


@login_required
@require_POST
def device_delete_data(request, device_id):
    device = get_object_or_404(UserDevice, pk=device_id, user=request.user)
    DigitalSummary.objects.filter(device_report__device=device, user=request.user).delete(); messages.success(request, "Data attributed to this device was deleted."); return redirect("core:devices")


@csrf_exempt
@require_POST
def api_pair_device(request):
    ip = request.META.get("REMOTE_ADDR", "unknown")
    if not throttle(f"pair:{ip}", limit=10, seconds=900): return JsonResponse({"error": "rate_limited"}, status=429)
    try: payload = _json_body(request)
    except ValueError as exc: return JsonResponse({"error": str(exc)}, status=400)
    code = str(payload.get("pairing_code", "")).strip().upper()
    pairing = DevicePairingCode.objects.filter(code_hash=hash_secret(code), consumed_at__isnull=True, expires_at__gte=timezone.now()).select_related("user").first()
    if not pairing: return JsonResponse({"error": "invalid_or_expired_pairing_code"}, status=400)
    if payload.get("consent_version") != pairing.consent_version: return JsonResponse({"error": "consent_required"}, status=400)
    raw, digest = issue_device_token()
    with transaction.atomic():
        pairing = DevicePairingCode.objects.select_for_update().get(pk=pairing.pk)
        if pairing.consumed_at: return JsonResponse({"error": "pairing_code_used"}, status=409)
        device = UserDevice.objects.create(user=pairing.user, name=str(payload.get("name") or "Android companion")[:120], platform=payload.get("platform") if payload.get("platform") in ("android", "ios") else "android", app_version=str(payload.get("app_version") or "")[:32], device_model=str(payload.get("device_model") or "")[:80], os_version=str(payload.get("os_version") or "")[:32], token_hash=digest, consent_version=pairing.consent_version, consent_accepted_at=timezone.now())
        pairing.consumed_at = timezone.now(); pairing.save(update_fields=["consumed_at"])
    return JsonResponse({"accepted": True, "device_id": str(device.public_id), "device_token": raw, "schema_version": DEVICE_SCHEMA_VERSION})


@csrf_exempt
@require_POST
def api_mobile_analytics(request):
    device = authenticate_device(request)
    if not device: return JsonResponse({"error": "invalid_device_credential"}, status=401)
    if not throttle(f"sync:{device.public_id}", limit=60, seconds=3600): return JsonResponse({"error": "rate_limited"}, status=429)
    now = timezone.now(); device.last_seen_at = now; device.last_sync_attempt_at = now; device.save(update_fields=["last_seen_at", "last_sync_attempt_at"])
    try: payload = _json_body(request); schema = int(payload.get("schema_version", 0))
    except (ValueError, TypeError): device.last_sync_status = "failed"; device.last_sync_error_code = "invalid_payload"; device.save(update_fields=["last_sync_status", "last_sync_error_code"]); return JsonResponse({"error": "invalid_payload"}, status=400)
    if schema != DEVICE_SCHEMA_VERSION: return JsonResponse({"error": "unsupported_schema", "supported_schema_versions": [DEVICE_SCHEMA_VERSION], "minimum_supported_schema": DEVICE_SCHEMA_VERSION}, status=426)
    report_id = str(payload.get("device_report_id") or "")[:120]
    if not report_id: return JsonResponse({"error": "device_report_id_required"}, status=400)
    existing = DeviceAnalyticsReport.objects.filter(device=device, device_report_id=report_id).select_related("summary").first()
    if existing: return JsonResponse({"accepted": True, "summary_id": existing.summary_id, "assessment_generated": bool(existing.summary.mobile_assessment_snapshot), "idempotent": True, "sync_timestamp": existing.received_at.isoformat()})
    try: analytics = normalize_mobile_analytics(payload)
    except (TypeError, ValueError): return JsonResponse({"error": "invalid_analytics"}, status=400)
    total = analytics.get("total_minutes")
    if total is None: return JsonResponse({"error": "total_minutes_required"}, status=400)
    with transaction.atomic():
        try:
            summary = DigitalSummary.objects.create(user=device.user, screen_time_minutes=total, wellness_score=max(0, min(100, 100 - int(total / 8))), category="Balanced" if total < 300 else "High", insight="Synced Mobile Analytics report.", app_usage=analytics.get("apps", []), mobile_analytics_snapshot=analytics)
            summary.goal_rescue_snapshot = freeze_goal_rescue_snapshot(build_goal_rescue(device.user, total)); summary.mobile_assessment_snapshot = build_mobile_analytics_assessment(summary); summary.save(update_fields=["goal_rescue_snapshot", "mobile_assessment_snapshot"]); ensure_goal_rescue_outcome(summary)
            DeviceAnalyticsReport.objects.create(device=device, summary=summary, device_report_id=report_id, schema_version=schema)
        except IntegrityError:
            existing = DeviceAnalyticsReport.objects.get(device=device, device_report_id=report_id); summary = existing.summary
    device.last_successful_sync_at = timezone.now(); device.last_sync_status = "success"; device.last_sync_error_code = ""; device.save(update_fields=["last_successful_sync_at", "last_sync_status", "last_sync_error_code"])
    return JsonResponse({"accepted": True, "summary_id": summary.pk, "assessment_generated": True, "idempotent": False, "sync_timestamp": device.last_successful_sync_at.isoformat()})


@csrf_exempt
@require_POST
def api_rotate_device_token(request):
    device = authenticate_device(request)
    if not device: return JsonResponse({"error": "invalid_device_credential"}, status=401)
    raw, digest = issue_device_token(); device.token_hash = digest; device.token_rotated_at = timezone.now(); device.save(update_fields=["token_hash", "token_rotated_at"])
    return JsonResponse({"device_token": raw, "rotated_at": device.token_rotated_at.isoformat()})


@csrf_exempt
@require_POST
def api_revoke_device(request):
    device = authenticate_device(request)
    if not device: return JsonResponse({"error": "invalid_device_credential"}, status=401)
    device.is_active = False; device.revoked_at = timezone.now()
    device.save(update_fields=["is_active", "revoked_at"])
    record_security_event(device.user, "device_revoked", device=str(device.public_id))
    return JsonResponse({"revoked": True})


@require_GET
def api_compatibility(request):
    return JsonResponse({"minimum_supported_android_version": "1.0.0", "latest_supported_schema_version": DEVICE_SCHEMA_VERSION, "supported_schema_versions": [DEVICE_SCHEMA_VERSION]})
