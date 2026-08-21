import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import DeviceAnalyticsReport, InAppNotification, Reminder, ScreenTimeTarget, UserAppPreference, UserDevice
from .platform_services import CONSENT_VERSION, dispatch_due_reminders, hash_secret, issue_device_token


class PlatformWebTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("platform", "platform@example.com", "Strong-pass-123")
        self.other = User.objects.create_user("other-platform", "other@example.com", "Strong-pass-123")
        self.client.force_login(self.user)

    def test_ready_is_generic(self):
        self.client.logout(); response = self.client.get(reverse("core:ready"))
        self.assertEqual(response.json(), {"status": "ready"})

    def test_app_preference_is_user_scoped(self):
        response = self.client.post(reverse("core:app_preference_edit", args=["youtube"]), {"display_name": "YouTube", "category": "Education", "purpose": "Useful", "linked_goal": ""})
        self.assertRedirects(response, reverse("core:app_preferences"))
        self.assertTrue(UserAppPreference.objects.filter(user=self.user, normalized_app_name="youtube").exists())
        self.assertFalse(UserAppPreference.objects.filter(user=self.other).exists())

    def test_target_validation_and_ownership(self):
        response = self.client.post(reverse("core:targets"), {"target_type": "app", "key": "Example", "daily_minutes": 30, "enabled": "on"})
        self.assertRedirects(response, reverse("core:targets"))
        target = ScreenTimeTarget.objects.get(user=self.user)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("core:target_delete", args=[target.pk])).status_code, 404)

    def test_notifications_are_post_only_and_scoped(self):
        notification = InAppNotification.objects.create(user=self.user, notification_type="goal", title="Goal", message="Continue")
        self.assertEqual(self.client.get(reverse("core:notification_read", args=[notification.pk])).status_code, 405)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("core:notification_read", args=[notification.pk])).status_code, 404)

    def test_reminder_dispatch_respects_opt_in_and_dedupes(self):
        profile = self.user.userprofile; profile.reminders_enabled = True; profile.in_app_reminders = True; profile.save()
        Reminder.objects.create(user=self.user, reminder_type="weekly", title="Weekly Review", message="Review", due_at=timezone.now()-timedelta(minutes=1))
        self.assertEqual(dispatch_due_reminders(), 1)
        self.assertEqual(dispatch_due_reminders(), 0)
        self.assertEqual(self.user.notifications.count(), 1)

    def test_monthly_exports_are_scoped_and_safe(self):
        self.user.digital_summaries.create(screen_time_minutes=120, wellness_score=80, category="Balanced", insight="=formula")
        self.other.digital_summaries.create(screen_time_minutes=999, wellness_score=1, category="High", insight="other")
        csv_response = self.client.get(reverse("core:monthly_review_csv"))
        self.assertEqual(csv_response["Content-Type"], "text/csv; charset=utf-8")
        self.assertNotIn("999", csv_response.content.decode())
        pdf = self.client.get(reverse("core:monthly_review_pdf"))
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_search_is_private_and_escapes_query(self):
        self.user.goals.create(title="My <script>Goal", why_it_matters="x", progress_unit="tasks", weekly_target=1)
        self.other.goals.create(title="Private Secret", why_it_matters="x", progress_unit="tasks", weekly_target=1)
        response = self.client.get(reverse("core:search"), {"q": "<script>"})
        self.assertContains(response, "&lt;script&gt;", html=False)
        self.assertNotContains(response, "Private Secret")

    def test_delete_summary_is_post_only_and_scoped(self):
        summary = self.other.digital_summaries.create(screen_time_minutes=10, wellness_score=90, category="Balanced", insight="x")
        self.assertEqual(self.client.get(reverse("core:delete_summary", args=[summary.pk])).status_code, 405)
        self.assertEqual(self.client.post(reverse("core:delete_summary", args=[summary.pk])).status_code, 404)

    def test_report_delete_confirmation_is_scoped(self):
        own = self.user.digital_summaries.create(screen_time_minutes=10, wellness_score=90, category="Balanced", insight="x")
        other = self.other.digital_summaries.create(screen_time_minutes=10, wellness_score=90, category="Balanced", insight="x")
        self.assertContains(self.client.get(reverse("core:delete_summary_confirm", args=[own.pk])), "Delete this report")
        self.assertEqual(self.client.get(reverse("core:delete_summary_confirm", args=[other.pk])).status_code, 404)

    def test_account_delete_requires_password_and_phrase(self):
        response = self.client.post(reverse("core:delete_account"), {"password": "wrong", "confirm": "DELETE"})
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        response = self.client.post(reverse("core:delete_account"), {"password": "Strong-pass-123", "confirm": "DELETE"})
        self.assertRedirects(response, reverse("core:home"))
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())


class DeviceApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("device-owner", "device@example.com", "Strong-pass-123")
        self.client.force_login(self.user)

    def _pair(self):
        self.client.post(reverse("core:device_pairing_create"), {"consent": "yes"})
        code = self.client.session["pairing_code"]
        self.client.logout()
        return self.client.post(reverse("core:api_pair_device"), data=json.dumps({"pairing_code": code, "consent_version": CONSENT_VERSION, "platform": "android", "name": "Phone", "app_version": "1.0.0"}), content_type="application/json")

    def test_pairing_is_single_use_and_token_hashed(self):
        response = self._pair(); self.assertEqual(response.status_code, 200)
        token = response.json()["device_token"]
        device = UserDevice.objects.get(user=self.user)
        self.assertNotEqual(device.token_hash, token); self.assertEqual(device.token_hash, hash_secret(token))

    def test_sync_is_idempotent_and_user_derived_from_token(self):
        paired = self._pair().json(); token = paired["device_token"]
        payload = {"schema_version": 1, "device_report_id": "report-1", "user_id": 999999, "report_date": timezone.localdate().isoformat(), "total_minutes": 90, "apps": [{"name": "Study", "minutes": 30}], "source_type": "android_device_sync"}
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        first = self.client.post(reverse("core:api_mobile_analytics"), data=json.dumps(payload), content_type="application/json", **headers)
        second = self.client.post(reverse("core:api_mobile_analytics"), data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(first.status_code, 200); self.assertTrue(second.json()["idempotent"])
        self.assertEqual(DeviceAnalyticsReport.objects.count(), 1)
        summary = DeviceAnalyticsReport.objects.get().summary
        self.assertEqual(summary.user, self.user)
        self.assertEqual(summary.ingestion_source, summary.SOURCE_DEVICE_SYNC)
        self.assertEqual(summary.total_basis, summary.TOTAL_DEVICE)
        self.assertFalse(summary.was_user_confirmed)

    def test_unsupported_schema_and_revoked_token(self):
        paired = self._pair().json(); token = paired["device_token"]
        response = self.client.post(reverse("core:api_mobile_analytics"), data=json.dumps({"schema_version": 99, "device_report_id": "x"}), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 426)
        device = UserDevice.objects.get(user=self.user); device.is_active = False; device.save()
        response = self.client.post(reverse("core:api_mobile_analytics"), data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 401)

    def test_rotation_invalidates_old_token(self):
        paired = self._pair().json(); old = paired["device_token"]
        response = self.client.post(reverse("core:api_rotate_device_token"), data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {old}")
        self.assertEqual(response.status_code, 200); new = response.json()["device_token"]
        self.assertEqual(self.client.post(reverse("core:api_rotate_device_token"), data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {old}").status_code, 401)
        self.assertEqual(self.client.post(reverse("core:api_rotate_device_token"), data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {new}").status_code, 200)

    def test_companion_can_revoke_itself(self):
        token = self._pair().json()["device_token"]
        response = self.client.post(reverse("core:api_revoke_device"), data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserDevice.objects.get(user=self.user).is_active)

    def test_device_data_delete_preserves_manual_report(self):
        paired = self._pair().json(); token = paired["device_token"]
        payload = {"schema_version": 1, "device_report_id": "report-delete", "total_minutes": 30, "apps": []}
        self.client.post(reverse("core:api_mobile_analytics"), data=json.dumps(payload), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        manual = self.user.digital_summaries.create(screen_time_minutes=12, wellness_score=90, category="Balanced", insight="manual")
        self.client.force_login(self.user); device = UserDevice.objects.get(user=self.user)
        self.client.post(reverse("core:device_delete_data", args=[device.pk]))
        self.assertTrue(self.user.digital_summaries.filter(pk=manual.pk).exists())
        self.assertFalse(DeviceAnalyticsReport.objects.exists())


class EmailAndCommandTests(TestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_registration_issues_verification_without_exposing_token(self):
        response = self.client.post(reverse("core:register"), {"username": "verified", "email": "verify@example.com", "password1": "Strong-pass-123", "password2": "Strong-pass-123"})
        self.assertRedirects(response, reverse("core:dashboard")); self.assertEqual(len(mail.outbox), 1)
        user = User.objects.get(username="verified"); self.assertFalse(user.userprofile.email_verified_at)

    def test_cleanup_commands_run(self):
        call_command("cleanup_pairing_codes"); call_command("cleanup_notifications")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_change_keeps_old_address_until_verified(self):
        user = User.objects.create_user("email-change", "old@example.com", "Strong-pass-123")
        self.client.force_login(user)
        self.client.post(reverse("core:email_change"), {"email": "new@example.com"})
        user.refresh_from_db(); self.assertEqual(user.email, "old@example.com"); self.assertEqual(user.userprofile.pending_email, "new@example.com")
        token = mail.outbox[-1].body.rsplit("/", 2)[-2]
        self.client.get(reverse("core:verify_email", args=[token]))
        user.refresh_from_db(); self.assertEqual(user.email, "new@example.com")
