from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import DigitalSummary, UserAppPreference
from core.report_quality import reconcile_known_apps, report_data_quality
from services.screenshot_drafts import coverage_summary, create_draft, merge_into_draft
from services.ocr_runtime import OCRRuntime, OCR_AVAILABLE
from services.screenshot_ingestion import extract_screenshot
from services.screen_time_parser import parse_screenshot_text
from .test_screenshot_ingestion import image_upload


def extraction(*, total=None, apps=None, report_date=None, **metrics):
    result = {
        "status": "SUCCESS", "provider": "ANDROID_DIGITAL_WELLBEING", "confidence": "MEDIUM",
        "source_type": "android_digital_wellbeing", "platform": "android",
        "detection_confidence": "medium", "total_minutes": total, "total_screen_time": total,
        "apps": apps or [], "warnings": [],
    }
    if report_date:
        result["report_date"] = report_date
    result.update(metrics)
    return result


def review_payload(total="100", apps=None):
    apps = apps if apps is not None else [("YouTube", 40)]
    data = {
        "report_date": timezone.localdate().isoformat(), "total_minutes": total,
        "pickups": "", "unlocks": "", "notifications": "", "sessions": "",
        "longest_session_minutes": "", "apps-TOTAL_FORMS": str(len(apps)),
        "apps-INITIAL_FORMS": str(len(apps)), "apps-MIN_NUM_FORMS": "0", "apps-MAX_NUM_FORMS": "50",
    }
    for index, (name, minutes) in enumerate(apps):
        data[f"apps-{index}-name"] = name
        data[f"apps-{index}-minutes"] = str(minutes)
        data[f"apps-{index}-review_state"] = "detected"
    return data


class ReportProvenanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("quality", password="test-password")
        self.client.force_login(self.user)

    def test_legacy_defaults_are_safe(self):
        summary = DigitalSummary.objects.create(user=self.user, screen_time_minutes=50, wellness_score=90, category="Balanced", insight="legacy")
        quality = report_data_quality(summary)
        self.assertEqual(quality.ingestion_source, DigitalSummary.SOURCE_LEGACY)
        self.assertEqual(quality.total_basis, DigitalSummary.TOTAL_LEGACY)
        self.assertFalse(quality.supports_total_analysis)

    def test_manual_report_is_user_entered_and_confirmed(self):
        self.client.post(reverse("core:home"), {"screen_time": 90, "mood": "Calm", "goal": "Study"})
        summary = DigitalSummary.objects.get(user=self.user)
        self.assertEqual(summary.ingestion_source, DigitalSummary.SOURCE_MANUAL)
        self.assertEqual(summary.total_basis, DigitalSummary.TOTAL_USER)
        self.assertTrue(summary.was_user_confirmed)
        self.assertTrue(report_data_quality(summary).supports_total_analysis)

    def test_quality_preserves_official_vs_derived_semantics(self):
        official = DigitalSummary.objects.create(
            user=self.user, screen_time_minutes=300, wellness_score=70, category="Balanced", insight="x",
            ingestion_source=DigitalSummary.SOURCE_SCREENSHOT, total_basis=DigitalSummary.TOTAL_OFFICIAL,
            was_user_confirmed=True,
            mobile_analytics_snapshot={"total_minutes": 300, "apps": [{"name": "A", "minutes": 240}], "detection_confidence": "medium"},
        )
        quality = report_data_quality(official)
        self.assertTrue(quality.has_official_total)
        self.assertEqual(quality.app_coverage_ratio, 80.0)
        derived = DigitalSummary.objects.create(
            user=self.user, screen_time_minutes=100, wellness_score=80, category="Balanced", insight="x",
            ingestion_source=DigitalSummary.SOURCE_SCREENSHOT, total_basis=DigitalSummary.TOTAL_APP_SUM,
            was_user_confirmed=True, mobile_analytics_snapshot={"apps": [{"name": "A", "minutes": 100}]},
        )
        derived_quality = report_data_quality(derived)
        self.assertFalse(derived_quality.has_official_total)
        self.assertIsNone(derived_quality.official_total_minutes)
        self.assertFalse(derived_quality.supports_total_analysis)
        self.assertTrue(derived_quality.supports_app_analysis)

    def test_coverage_edges(self):
        self.assertEqual(coverage_summary(300, [{"minutes": 240}])["ratio"], 80.0)
        self.assertIsNone(coverage_summary(None, [{"minutes": 240}])["ratio"])
        self.assertTrue(coverage_summary(300, [{"minutes": 330}])["exceeds_total"])
        self.assertIsNone(coverage_summary(0, [{"minutes": 20}])["ratio"])

    def test_conservative_user_specific_app_reconciliation(self):
        UserAppPreference.objects.create(user=self.user, normalized_app_name="youtube", display_name="YouTube", category="Education")
        self.assertEqual(reconcile_known_apps(self.user, [{"name": "You Tube", "minutes": 20}])[0]["name"], "YouTube")
        self.assertEqual(reconcile_known_apps(self.user, [{"name": "YouTuber", "minutes": 20}])[0]["name"], "YouTuber")


class DraftMergeTests(TestCase):
    def test_total_and_app_screens_merge(self):
        draft = create_draft(extraction(total=320, pickups=48), "a", filename="a.png", mood="Calm", goal="Study", report_date="2026-08-21")
        merged, status = merge_into_draft(draft, extraction(apps=[{"name": "Instagram", "minutes": 80}, {"name": "YouTube", "minutes": 58}, {"name": "WhatsApp", "minutes": 44}]), "b")
        self.assertEqual(status, "merged")
        self.assertEqual(merged["extraction"]["total_minutes"], 320)
        self.assertEqual(len(merged["extraction"]["apps"]), 3)
        self.assertEqual(merged["screenshot_count"], 2)

    def test_duplicate_and_same_app_same_duration_are_deduplicated(self):
        draft = create_draft(extraction(apps=[{"name": "YouTube", "minutes": 40}]), "same", filename="a.png", mood="Calm", goal="Study", report_date="2026-08-21")
        unchanged, status = merge_into_draft(draft, extraction(apps=[]), "same")
        self.assertEqual(status, "duplicate")
        merged, status = merge_into_draft(draft, extraction(apps=[{"name": " youtube ", "minutes": 40}]), "other")
        self.assertEqual(len(merged["extraction"]["apps"]), 1)
        self.assertFalse(merged["conflicts"])

    def test_app_total_metric_and_date_conflicts_are_flagged(self):
        draft = create_draft(extraction(total=300, apps=[{"name": "Instagram", "minutes": 60}], notifications=80), "a", filename="a.png", mood="Calm", goal="Study", report_date="2026-08-21")
        merged, _ = merge_into_draft(draft, extraction(total=310, apps=[{"name": "Instagram", "minutes": 70}], notifications=90), "b")
        fields = [item["field"] for item in merged["conflicts"]]
        self.assertIn("total_minutes", fields)
        self.assertIn("notifications", fields)
        self.assertIn("app", fields)
        self.assertEqual(merged["extraction"]["apps"][0]["review_state"], "conflict")
        dated = create_draft(extraction(total=100, report_date="2026-08-20"), "x", filename="x.png", mood="Calm", goal="Study", report_date="2026-08-20")
        dated, status = merge_into_draft(dated, extraction(total=100, report_date="2026-08-21"), "y")
        self.assertEqual(status, "date_conflict")
        self.assertEqual(dated["screenshot_count"], 1)

    def test_server_limit_is_enforced(self):
        draft = create_draft(extraction(total=100), "0", filename="x.png", mood="Calm", goal="Study", report_date="2026-08-21")
        for index in range(1, 5):
            draft, self_status = merge_into_draft(draft, extraction(apps=[{"name": f"App {index}", "minutes": index}]), str(index))
            self.assertEqual(self_status, "merged")
        draft, status = merge_into_draft(draft, extraction(apps=[{"name": "Extra", "minutes": 1}]), "5")
        self.assertEqual(status, "limit")
        self.assertEqual(draft["screenshot_count"], 5)


class RealisticSyntheticLayoutTests(TestCase):
    @patch("services.screenshot_ingestion.resolve_tesseract", return_value=OCRRuntime(OCR_AVAILABLE, "test-tesseract"))
    @patch("services.screenshot_ingestion.configure_pytesseract")
    def test_light_dark_samsung_ios_cropped_and_total_only_layouts(self, configure, _resolve):
        cases = [
            ((245, 245, 245), "Digital Wellbeing\nDashboard\nScreen time 4 hr 20 min\nInstagram 80 min", "ANDROID_DIGITAL_WELLBEING", True, 1),
            ((12, 15, 20), "Digital Wellbeing and parental controls\nDevice Care\nScreen time 5 hr\nApp timers", "SAMSUNG_DIGITAL_WELLBEING", True, 0),
            ((245, 245, 245), "Screen Time\nDaily Average 3 hr 10 min\nMost Used\nMessages 40 min\nPickups 22", "IOS_SCREEN_TIME", True, 1),
            ((245, 245, 245), "App usage\nMost Used\nInstagram 60 min\nYouTube\n40 min", "GENERIC_USAGE_ANALYTICS", False, 2),
            ((12, 15, 20), "Screen time\n2 hr 15 min", "GENERIC_USAGE_ANALYTICS", True, 0),
        ]
        for color, text, provider, has_total, app_count in cases:
            with self.subTest(provider=provider, text=text[:12]):
                configure.return_value = type("Engine", (), {"image_to_string": lambda self, *args, **kwargs: text})()
                result = extract_screenshot(image_upload(color=color), parse_screenshot_text)
                self.assertEqual(result.provider, provider)
                self.assertEqual(result.total_screen_minutes is not None, has_total)
                self.assertEqual(len(result.apps), app_count)


class MultiScreenshotFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("multi", password="test-password")
        self.client.force_login(self.user)

    @patch("core.views.parse_screen_time_report")
    def test_two_screens_build_one_confirmed_report(self, parser):
        parser.side_effect = [extraction(total=320, pickups=48), extraction(apps=[{"name": "Instagram", "minutes": 80}, {"name": "YouTube", "minutes": 58}])]
        start = self.client.post(reverse("core:home"), {"file": image_upload(color=(245, 245, 245)), "mood": "Calm", "goal": "Study"})
        review_url = start.url
        token = review_url.rstrip("/").split("/")[-1]
        added = self.client.post(reverse("core:screenshot_review_add", args=[token]), {"file": image_upload(color=(20, 20, 20))})
        self.assertRedirects(added, review_url)
        page = self.client.get(review_url)
        self.assertContains(page, "2 of 5 screenshots")
        self.assertContains(page, "43%")
        saved = self.client.post(review_url, review_payload("320", [("Instagram", 80), ("YouTube", 58)]))
        self.assertRedirects(saved, reverse("core:summary"))
        self.assertEqual(DigitalSummary.objects.count(), 1)

    @patch("core.views.parse_screen_time_report")
    def test_exact_duplicate_is_not_reprocessed(self, parser):
        parser.return_value = extraction(total=100)
        upload = image_upload()
        start = self.client.post(reverse("core:home"), {"file": upload, "mood": "Calm", "goal": "Study"})
        token = start.url.rstrip("/").split("/")[-1]
        duplicate = self.client.post(reverse("core:screenshot_review_add", args=[token]), {"file": image_upload()}, follow=True)
        self.assertContains(duplicate, "This screenshot is already included", status_code=200)
        self.assertEqual(parser.call_count, 1)

    @patch("core.views.parse_screen_time_report", return_value=extraction(total=100, apps=[{"name": "App", "minutes": 40}]))
    def test_confirmation_is_idempotent_per_draft(self, _):
        start = self.client.post(reverse("core:home"), {"file": image_upload(), "mood": "Calm", "goal": "Study"})
        first = self.client.post(start.url, review_payload())
        self.assertEqual(first.status_code, 302)
        second = self.client.post(start.url, review_payload())
        self.assertRedirects(second, reverse("core:home"))
        self.assertEqual(DigitalSummary.objects.count(), 1)

    @patch("core.views.parse_screen_time_report", return_value=extraction(total=100))
    def test_draft_token_is_isolated_to_its_session(self, _):
        start = self.client.post(reverse("core:home"), {"file": image_upload(), "mood": "Calm", "goal": "Study"})
        other = Client()
        response = other.get(start.url)
        self.assertRedirects(response, reverse("core:home"))
