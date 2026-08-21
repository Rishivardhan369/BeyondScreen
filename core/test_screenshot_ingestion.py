from io import BytesIO
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from core.models import DigitalSummary
from services.image_preprocessing import InvalidImage, build_variants, load_safe_image
from services.ocr_runtime import OCR_ENGINE_UNAVAILABLE, OCR_LIBRARY_UNAVAILABLE, resolve_tesseract
from services.screen_time_parser import (
    detect_screenshot_provider,
    normalize_minutes,
    normalize_ocr_text,
    parse_screenshot_text,
)
from services.screenshot_ingestion import score_ocr_text


def image_upload(name="screen.png", color=(245, 245, 245), size=(500, 900)):
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


class DurationAndTextTests(TestCase):
    def test_supported_duration_forms(self):
        cases = {
            "1h": 60, "1 h": 60, "1 hr": 60, "1 hour": 60,
            "1h 30m": 90, "1 h 30 m": 90, "1 hr 30 min": 90,
            "1 hour 30 minutes": 90, "90m": 90, "90 min": 90,
            "90 mins": 90, "0h 45m": 45, "23 min": 23,
            "I hr 2O min": 80,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_minutes(value), expected)

    def test_invalid_and_absurd_durations(self):
        for value in ("", "later", "-5 min", "25 hours", "9999 min", None):
            with self.subTest(value=value):
                self.assertIsNone(normalize_minutes(value))

    def test_text_normalization_does_not_rewrite_app_names(self):
        value = normalize_ocr_text("  Instagram\u00a0 OI  \r\nYouTube：  55 min •")
        self.assertIn("Instagram OI", value)
        self.assertIn("YouTube: 55 min", value)


class ProviderAndExtractionTests(TestCase):
    def test_provider_detection(self):
        cases = {
            "Digital Wellbeing Dashboard Screen time Unlocks Focus mode": "ANDROID_DIGITAL_WELLBEING",
            "Digital Wellbeing and parental controls Device Care App timers Screen time": "SAMSUNG_DIGITAL_WELLBEING",
            "Screen Time Daily Average Most Used Pickups Categories": "IOS_SCREEN_TIME",
            "App usage Screen time": "GENERIC_USAGE_ANALYTICS",
            "A landscape holiday photograph": "UNKNOWN",
        }
        for text, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(detect_screenshot_provider(text)["provider"], expected)

    def test_same_line_and_adjacent_line_apps(self):
        parsed = parse_screenshot_text(
            "Digital Wellbeing\nScreen time 4 hr 20 min\nInstagram 1 hr 20 min\n"
            "YouTube 55 min\nWhatsApp\n34 min\nNotifications 72\nUnlocks 31"
        )
        self.assertEqual(parsed["total_minutes"], 260)
        self.assertEqual([(a["name"], a["minutes"]) for a in parsed["apps"]],
                         [("Instagram", 80), ("YouTube", 55), ("WhatsApp", 34)])
        self.assertEqual(parsed["notifications"], 72)
        self.assertEqual(parsed["unlocks"], 31)

    def test_partial_total_only_apps_only_and_one_app_are_recoverable(self):
        total = parse_screenshot_text("Screen time\n5 hr")
        self.assertTrue(total["has_analytics"])
        self.assertEqual(total["total_minutes"], 300)
        apps = parse_screenshot_text("Most used\nInstagram\n1 hr 10 min\nYouTube 50 min")
        self.assertTrue(apps["has_analytics"])
        self.assertIsNone(apps["total_minutes"])
        self.assertEqual(sum(row["minutes"] for row in apps["apps"]), 120)
        one = parse_screenshot_text("Instagram\n20 min")
        self.assertTrue(one["has_analytics"])
        self.assertEqual(one["confidence"], "LOW")

    def test_wrong_content_has_no_analytics(self):
        self.assertFalse(parse_screenshot_text("Family holiday by the sea")["has_analytics"])

    def test_official_total_and_recognized_sum_remain_distinct(self):
        parsed = parse_screenshot_text("Screen time 3 hr\nInstagram 80 min\nYouTube 45 min")
        self.assertEqual(parsed["total_minutes"], 180)
        self.assertEqual(sum(app["minutes"] for app in parsed["apps"]), 125)

    def test_candidate_scoring_prefers_analytics_not_long_garbage(self):
        useful = score_ocr_text("Digital Wellbeing\nScreen time 3 hr\nInstagram 45 min")
        garbage = score_ocr_text("!@#$%^&* " * 40)
        self.assertGreater(useful, garbage)


class ImageAndRuntimeTests(TestCase):
    def test_valid_image_and_dark_mode_variant(self):
        loaded = load_safe_image(image_upload(color=(10, 12, 16)))
        names = [variant.name for variant in build_variants(loaded)]
        self.assertIn("dark_mode_inverted", names)
        self.assertLessEqual(len(names), 4)

    def test_corrupt_and_renamed_non_image_rejected(self):
        for payload in (b"", b"this is not a png"):
            with self.subTest(payload=payload):
                with self.assertRaises(InvalidImage):
                    load_safe_image(SimpleUploadedFile("screen.png", payload))

    @patch.dict("os.environ", {"TESSERACT_CMD": ""})
    @patch("services.ocr_runtime.shutil.which", return_value=None)
    @patch("pathlib.Path.is_file", return_value=False)
    def test_missing_native_engine_is_typed(self, *_):
        runtime = resolve_tesseract()
        self.assertIn(runtime.status, (OCR_ENGINE_UNAVAILABLE, OCR_LIBRARY_UNAVAILABLE))


class ScreenshotReviewFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ocr-user", password="test-password")
        self.client.force_login(self.user)
        self.extraction = {
            "status": "SUCCESS", "provider": "ANDROID_DIGITAL_WELLBEING", "confidence": "LOW",
            "source_type": "android_digital_wellbeing", "platform": "android",
            "detection_confidence": "low", "total_minutes": 260, "total_screen_time": 260,
            "recognized_app_total_minutes": 135,
            "apps": [{"name": "YouTube", "minutes": 65, "category": None}, {"name": "Instagram", "minutes": 70, "category": None}],
            "pickups": 30, "notifications": 80, "warnings": ["Automatic extraction was partial."],
        }

    @patch("core.views.parse_screen_time_report")
    def test_upload_review_correction_and_save(self, parser):
        parser.return_value = self.extraction
        response = self.client.post(reverse("core:home"), {"file": image_upload(), "mood": "Calm", "goal": "Study"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/analytics/screenshot-review/", response.url)
        review_url = response.url
        review = self.client.get(review_url)
        self.assertContains(review, "Low confidence")
        response = self.client.post(review_url, {
            "report_date": "2026-08-21", "total_minutes": "240", "pickups": "31", "notifications": "75",
            "unlocks": "", "sessions": "", "longest_session_minutes": "",
            "apps-TOTAL_FORMS": "3", "apps-INITIAL_FORMS": "2", "apps-MIN_NUM_FORMS": "0", "apps-MAX_NUM_FORMS": "50",
            "apps-0-name": "YouTube", "apps-0-minutes": "55", "apps-0-DELETE": "",
            "apps-1-name": "Instagram", "apps-1-minutes": "70", "apps-1-DELETE": "on",
            "apps-2-name": "Notion", "apps-2-minutes": "25", "apps-2-DELETE": "",
        })
        self.assertRedirects(response, reverse("core:summary"))
        saved = DigitalSummary.objects.get(user=self.user)
        self.assertEqual(saved.screen_time_minutes, 240)
        self.assertEqual(saved.ingestion_source, DigitalSummary.SOURCE_SCREENSHOT)
        self.assertEqual(saved.total_basis, DigitalSummary.TOTAL_OFFICIAL)
        self.assertTrue(saved.was_user_confirmed)
        self.assertEqual(saved.mobile_analytics_snapshot["apps"], [
            {"name": "YouTube", "minutes": 55, "category": None},
            {"name": "Notion", "minutes": 25, "category": None},
        ])

    @patch("core.views.parse_screen_time_report")
    def test_apps_only_reaches_review_and_can_save(self, parser):
        extraction = dict(self.extraction, total_minutes=None, total_screen_time=None)
        parser.return_value = extraction
        response = self.client.post(reverse("core:home"), {"file": image_upload(), "mood": "Calm", "goal": "Study"})
        self.assertEqual(response.status_code, 302)
        review_url = response.url
        response = self.client.post(review_url, {
            "report_date": "2026-08-21", "total_minutes": "", "pickups": "", "unlocks": "", "notifications": "", "sessions": "", "longest_session_minutes": "",
            "apps-TOTAL_FORMS": "2", "apps-INITIAL_FORMS": "2", "apps-MIN_NUM_FORMS": "0", "apps-MAX_NUM_FORMS": "50",
            "apps-0-name": "YouTube", "apps-0-minutes": "50", "apps-1-name": "WhatsApp", "apps-1-minutes": "20",
        })
        self.assertRedirects(response, reverse("core:summary"))
        saved = DigitalSummary.objects.get(user=self.user)
        self.assertEqual(saved.screen_time_minutes, 70)
        self.assertNotIn("total_minutes", saved.mobile_analytics_snapshot)
        self.assertEqual(saved.ingestion_source, DigitalSummary.SOURCE_SCREENSHOT)
        self.assertEqual(saved.total_basis, DigitalSummary.TOTAL_APP_SUM)
        self.assertTrue(saved.was_user_confirmed)

    @patch("core.views.parse_screen_time_report", return_value={"status": "ENGINE_UNAVAILABLE", "apps": []})
    def test_engine_unavailable_has_manual_next_step_and_no_fake_report(self, _):
        response = self.client.post(reverse("core:home"), {"file": image_upload(), "mood": "Calm", "goal": "Study"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Automatic screenshot reading is unavailable")
        self.assertFalse(DigitalSummary.objects.exists())

    def test_review_requires_session_and_invalid_rows_do_not_save(self):
        self.assertRedirects(self.client.get(reverse("core:screenshot_review")), reverse("core:home"))
        session = self.client.session
        session["screenshot_review_drafts"] = {"safe-token": {
            "token": "safe-token", "extraction": self.extraction, "filename": "screen.png",
            "mood": "Calm", "goal": "Study", "digests": ["digest"], "screenshot_count": 1,
            "official_total_detected": True, "conflicts": [], "warnings": [],
        }}
        session["latest_screenshot_draft"] = "safe-token"
        session.save()
        response = self.client.post(reverse("core:screenshot_review"), {
            "report_date": "bad", "total_minutes": "", "apps-TOTAL_FORMS": "1", "apps-INITIAL_FORMS": "0",
            "apps-MIN_NUM_FORMS": "0", "apps-MAX_NUM_FORMS": "50", "apps-0-name": "YouTube", "apps-0-minutes": "-1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DigitalSummary.objects.exists())
