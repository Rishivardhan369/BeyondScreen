import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from services import screen_time_parser
from services.screen_time_parser import (
    detect_report_platform,
    normalize_minutes,
    normalize_mobile_analytics,
    parse_mobile_analytics_text,
    parse_screen_time_report,
)

from .admin import ActionableInputFeedbackAdmin, DigitalSummaryAdmin
from .forms import PostcardForm, UserProfileForm
from .mobile_analytics import (
    ASSESSMENT_VERSION,
    _distribution,
    _feedback_preferences,
    build_mobile_analytics_assessment,
    build_mobile_analytics_snapshot,
    build_mobile_insights,
    build_transient_mobile_assessment,
    build_weekly_mobile_analytics,
    data_quality_for,
)
from .models import (
    ActionableInputFeedback,
    DigitalSummary,
    GoalAction,
    MomentumEntry,
    UserGoal,
)


class MobileTestMixin:
    def create_user(self, username="mobile-user"):
        return User.objects.create_user(username=username, password="safe-password")

    def create_goal(self, user, title="Write dissertation", primary=True, status="active"):
        goal = UserGoal.objects.create(
            user=user,
            title=title,
            why_it_matters="Finish meaningful work",
            current_focus="Draft the next section",
            progress_unit="pages",
            weekly_target=10,
            is_primary=primary,
            status=status,
        )
        actions = [
            GoalAction.objects.create(goal=goal, size="minimum", title="Write one paragraph", duration_minutes=5, progress_value=1),
            GoalAction.objects.create(goal=goal, size="standard", title="Draft two pages", duration_minutes=20, progress_value=2),
            GoalAction.objects.create(goal=goal, size="deep", title="Complete a chapter section", duration_minutes=45, progress_value=5),
        ]
        return goal, actions

    def create_summary(self, user, *, total=240, days_ago=0, analytics=None, assessment=None):
        snapshot = analytics if analytics is not None else {
            "schema_version": 1,
            "source_type": "android_digital_wellbeing",
            "platform": "android",
            "detection_confidence": "high",
            "total_minutes": total,
            "apps": [],
        }
        summary = DigitalSummary.objects.create(
            user=user,
            screen_time_minutes=total,
            wellness_score=75,
            category="Good",
            insight="Recorded activity.",
            goal_rescue_snapshot={},
            app_usage=snapshot.get("apps", []),
            mobile_analytics_snapshot=snapshot,
            mobile_assessment_snapshot=assessment or {},
        )
        if days_ago:
            DigitalSummary.objects.filter(pk=summary.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
            summary.refresh_from_db()
        return summary

    def freeze(self, summary):
        summary.mobile_assessment_snapshot = build_mobile_analytics_assessment(summary)
        summary.save(update_fields=["mobile_assessment_snapshot"])
        return summary.mobile_assessment_snapshot


class MobileNormalizationTests(TestCase):
    def test_supported_duration_formats_and_invalid_values(self):
        expected = {"5h 40m": 340, "5 hr 40 min": 340, "340 minutes": 340, "1h": 60, "45m": 45}
        for value, minutes in expected.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_minutes(value), minutes)
        for value in ("tomorrow", "", None, -2, "-5m"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_minutes(value))

    def test_normalization_rejects_bad_rows_and_trims_names(self):
        result = normalize_mobile_analytics({
            "source_type": "csv", "total_minutes": 30, "pickups": -1,
            "apps": [{"name": "  Notion  ", "minutes": "20", "category": "Productivity"}, {"name": "Bad", "minutes": "nope"}, "bad"],
        })
        self.assertNotIn("pickups", result)
        self.assertEqual(result["apps"], [{"name": "Notion", "minutes": 20, "category": "Productivity"}])

    def test_platform_detection_is_conservative(self):
        android = detect_report_platform("Digital Wellbeing Dashboard Screen time Unlocks App timers")
        ios = detect_report_platform("Screen Time Daily Average Most Used Pickups Notifications Categories")
        self.assertEqual(android["platform"], "android")
        self.assertEqual(ios["platform"], "ios")
        self.assertIn(detect_report_platform("Screen time 2h 10m")["platform"], {"generic", "unknown"})
        self.assertNotIn(detect_report_platform("Dashboard Screen Time Pickups")["platform"], {"android", "ios"})

    def test_text_metric_extraction_and_partial_shapes(self):
        text = """Digital Wellbeing\nDashboard\nTotal Screen Time: 5h 40m\nUnlocks: 68\nNotifications: 124\nSessions: 20\nLongest session: 42m\nFirst use: 7:15 AM\nLatest use: 11:48 PM\nInstagram  1h 20m\nNotion  45m"""
        parsed = parse_mobile_analytics_text(text)
        self.assertEqual(parsed["total_minutes"], 340)
        self.assertEqual(parsed["pickups"], 68)
        self.assertEqual(parsed["notifications"], 124)
        self.assertEqual(parsed["sessions"], 20)
        self.assertEqual(parsed["longest_session_minutes"], 42)
        self.assertEqual(parsed["first_use_time"], "7:15 AM")
        self.assertEqual(parsed["last_use_time"], "11:48 PM")
        self.assertEqual({app["name"] for app in parsed["apps"]}, {"Instagram", "Notion"})
        malformed = parse_mobile_analytics_text("Screen Time: 45m\nLatest use: 99:99")
        self.assertNotIn("last_use_time", malformed)

    def test_text_and_csv_report_inputs(self):
        text_upload = SimpleUploadedFile("report.txt", b"Screen Time\n3h 15m\nPickups: 44")
        parsed = parse_screen_time_report(text_upload)
        self.assertEqual(parsed["total_minutes"], 195)
        self.assertEqual(parsed["pickups"], 44)
        csv_upload = SimpleUploadedFile("report.csv", b"app,minutes,category,pickups\nNotion,30,Productivity,22\nInstagram,45,Social,\n")
        parsed_csv = parse_screen_time_report(csv_upload)
        self.assertEqual(parsed_csv["total_minutes"], 75)
        self.assertEqual(parsed_csv["pickups"], 22)
        self.assertEqual(len(parsed_csv["apps"]), 2)

    def test_pdf_corrupt_and_optional_ocr_fall_back(self):
        self.assertIsNone(parse_screen_time_report(SimpleUploadedFile("report.pdf", b"not-pdf")))
        with patch.object(screen_time_parser, "TESSERACT_AVAILABLE", False):
            self.assertIsNone(parse_screen_time_report(SimpleUploadedFile("report.png", b"bad")))
        fake_image_module = Mock()
        fake_image_module.open.side_effect = OSError("corrupt")
        with patch.object(screen_time_parser, "TESSERACT_AVAILABLE", True), patch.object(screen_time_parser, "Image", fake_image_module):
            self.assertIsNone(parse_screen_time_report(SimpleUploadedFile("report.png", b"bad")))


class MobileAssessmentTests(MobileTestMixin, TestCase):
    def setUp(self):
        self.user = self.create_user()

    def test_data_quality_states(self):
        self.assertEqual(data_quality_for({"source_type": "manual", "total_minutes": 20, "apps": []}), "Manual only")
        self.assertEqual(data_quality_for({"source_type": "unknown", "apps": []}), "Limited")
        self.assertEqual(data_quality_for({"source_type": "csv", "total_minutes": 20, "apps": [{"name": "A", "minutes": 10}]}), "Partial")
        self.assertEqual(data_quality_for({"source_type": "csv", "total_minutes": 20, "apps": [{"name": "A", "minutes": 10}], "pickups": 2, "notifications": 3}), "Complete")

    def test_distribution_thresholds_caps_and_categories(self):
        for minutes, expected in ((39, "Distributed"), (40, "Moderately concentrated"), (65, "Highly concentrated")):
            remainder = 100 - minutes
            result = _distribution({"total_minutes": 100, "apps": [{"name": "Unknown Tool", "minutes": minutes}, {"name": "Other A", "minutes": remainder // 2}, {"name": "Other B", "minutes": remainder - remainder // 2}]})
            self.assertEqual(result["concentration"], expected)
        capped = _distribution({"total_minutes": 20, "apps": [{"name": "Notion", "minutes": 80, "category": "Productivity"}, {"name": "Mystery", "minutes": 30}]})
        self.assertEqual(capped["top_app_share"], 100)
        self.assertEqual(capped["top_three_share"], 100)
        self.assertEqual(capped["most_used_category"]["category"], "Productivity")
        self.assertIn("Unknown", {row["category"] for row in capped["categories"]})
        self.assertIsNone(_distribution({"total_minutes": 0, "apps": []})["concentration"])

    def test_recorded_day_baselines_missing_days_same_day_and_isolation(self):
        self.create_goal(self.user)
        other = self.create_user("other")
        self.create_summary(self.user, total=100, days_ago=3)
        self.create_summary(self.user, total=200, days_ago=3)
        self.create_summary(self.user, total=300, days_ago=1)
        self.create_summary(other, total=1000, days_ago=2)
        current = self.create_summary(self.user, total=300)
        assessment = build_mobile_analytics_assessment(current)
        comparison = assessment["screen_time"]["comparison"]["seven_day"]
        self.assertTrue(comparison["available"])
        self.assertEqual(comparison["average"], 225)
        self.assertEqual(comparison["difference"], 75)
        self.assertEqual(comparison["percentage_difference"], 33.3)
        self.assertEqual(comparison["sample_count"], 2)
        self.assertEqual(comparison["direction"], "above")

    def test_one_sample_is_insufficient(self):
        self.create_summary(self.user, total=100, days_ago=1)
        current = self.create_summary(self.user, total=200)
        comparison = build_mobile_analytics_assessment(current)["screen_time"]["comparison"]["seven_day"]
        self.assertFalse(comparison["available"])
        self.assertEqual(comparison["sample_count"], 1)

    def test_interaction_metrics_rates_comparisons_and_nulls(self):
        self.create_goal(self.user)
        for day, pickups, notifications, longest in ((3, 20, 40, 20), (2, 30, 50, 30)):
            self.create_summary(self.user, total=120, days_ago=day, analytics={"source_type": "csv", "total_minutes": 120, "apps": [], "pickups": pickups, "notifications": notifications, "longest_session_minutes": longest})
        current = self.create_summary(self.user, total=120, analytics={"source_type": "csv", "total_minutes": 120, "apps": [], "pickups": 50, "notifications": 80, "sessions": 4, "longest_session_minutes": 50})
        result = build_mobile_analytics_assessment(current)
        self.assertEqual(result["interaction_metrics"]["pickups_per_screen_hour"], 25)
        self.assertEqual(result["interaction_metrics"]["notifications_per_screen_hour"], 40)
        self.assertEqual(result["interaction_metrics"]["average_session_minutes"], 30)
        self.assertEqual(result["screen_time"]["comparison"]["pickups"]["direction"], "above")
        self.assertEqual(result["screen_time"]["comparison"]["notifications"]["direction"], "above")
        self.assertEqual(result["screen_time"]["comparison"]["longest_session_minutes"]["direction"], "above")
        zero = self.create_summary(self.user, total=0, analytics={"source_type": "manual", "total_minutes": 0, "apps": [], "pickups": 0})
        zero_result = build_mobile_analytics_assessment(zero)
        self.assertIsNone(zero_result["interaction_metrics"]["pickups_per_screen_hour"])
        self.assertIsNone(zero_result["interaction_metrics"]["notifications"])

    def test_below_close_and_late_usage_signals(self):
        self.create_goal(self.user)
        self.create_summary(self.user, total=200, days_ago=3)
        self.create_summary(self.user, total=200, days_ago=2)
        below = self.create_summary(self.user, total=100, analytics={"source_type": "ios_screen_time", "total_minutes": 100, "apps": [], "last_use_time": "12:18 AM"})
        signals = build_mobile_analytics_assessment(below)["usage_signals"]
        self.assertIn("Below recent baseline", {item["label"] for item in signals})
        self.assertIn("Late recorded use", {item["label"] for item in signals})
        close = self.create_summary(self.user, total=202)
        close_labels = {item["label"] for item in build_mobile_analytics_assessment(close)["usage_signals"]}
        self.assertIn("Close recent baseline", close_labels)

    def test_goal_context_inputs_priority_structure_and_reclaim_cap(self):
        goal, _actions = self.create_goal(self.user)
        self.user.userprofile.preferred_daily_screen_time_minutes = 100
        self.user.userprofile.save(update_fields=["preferred_daily_screen_time_minutes"])
        analytics = {"source_type": "android_digital_wellbeing", "platform": "android", "total_minutes": 300, "apps": [{"name": "Instagram", "minutes": 200}], "pickups": 60, "notifications": 90}
        summary = self.create_summary(self.user, total=300, analytics=analytics)
        assessment = build_mobile_analytics_assessment(summary)
        inputs = assessment["actionable_inputs"]
        self.assertEqual([item["type"] for item in inputs], ["personal_target", "goal_target", "app_concentration"])
        self.assertLessEqual(len(inputs), 3)
        for item in inputs:
            for key in ("id", "type", "title", "explanation", "source_signal", "recommended_action", "priority", "why"):
                self.assertIn(key, item)
        reclaim = inputs[2]
        self.assertEqual(reclaim["estimated_duration_minutes"], 20)
        self.assertLessEqual(reclaim["estimated_duration_minutes"], min(30, int(200 * .2)))
        self.assertEqual(assessment["goal_context"]["goal_id"], goal.id)
        self.assertEqual(assessment["assessment_version"], ASSESSMENT_VERSION)

    def test_no_primary_paused_completed_and_additional_are_not_context(self):
        extra, _ = self.create_goal(self.user, title="Secondary", primary=False)
        summary = self.create_summary(self.user)
        assessment = build_mobile_analytics_assessment(summary)
        self.assertFalse(assessment["goal_context"]["available"])
        self.assertEqual(assessment["actionable_inputs"][0]["type"], "goal_context")
        extra.is_primary = True
        extra.status = UserGoal.STATUS_PAUSED
        extra.save(update_fields=["is_primary", "status"])
        self.assertFalse(build_mobile_analytics_assessment(summary)["goal_context"]["available"])
        extra.status = UserGoal.STATUS_COMPLETED
        extra.save(update_fields=["status"])
        self.assertFalse(build_mobile_analytics_assessment(summary)["goal_context"]["available"])

    def test_snapshot_is_frozen_after_goal_and_preferences_change(self):
        goal, _ = self.create_goal(self.user)
        summary = self.create_summary(self.user, analytics={"source_type": "manual", "total_minutes": 200, "apps": []})
        original = self.freeze(summary)
        goal.title = "Changed later"
        goal.save(update_fields=["title"])
        self.user.userprofile.show_actionable_inputs = False
        self.user.userprofile.save(update_fields=["show_actionable_inputs"])
        summary.refresh_from_db()
        self.assertEqual(summary.mobile_assessment_snapshot, original)
        legacy = self.create_summary(self.user, total=10, analytics={})
        self.assertEqual(legacy.mobile_assessment_snapshot, {})

    def test_transient_assessment_has_no_personalization(self):
        result = build_transient_mobile_assessment({"source_type": "manual", "total_minutes": 40, "apps": []})
        self.assertFalse(result["screen_time"]["comparison"]["seven_day"]["available"])
        self.assertFalse(result["goal_context"]["available"])
        self.assertEqual(result["actionable_inputs"][0]["type"], "goal_context")

    def test_personal_target_absence_below_and_above(self):
        self.create_goal(self.user)
        no_target = self.create_summary(self.user, total=200)
        self.assertNotIn("personal_target", {item["type"] for item in build_mobile_analytics_assessment(no_target)["actionable_inputs"]})
        self.user.userprofile.preferred_daily_screen_time_minutes = 240
        self.user.userprofile.save(update_fields=["preferred_daily_screen_time_minutes"])
        below = self.create_summary(self.user, total=180)
        self.assertNotIn("personal_target", {item["type"] for item in build_mobile_analytics_assessment(below)["actionable_inputs"]})
        above = self.create_summary(self.user, total=300)
        self.assertEqual(build_mobile_analytics_assessment(above)["actionable_inputs"][0]["type"], "personal_target")

    def test_feedback_personalization_threshold_and_user_isolation(self):
        self.assertEqual(_feedback_preferences(self.user), {})
        for index in range(2):
            summary = self.create_summary(self.user)
            ActionableInputFeedback.objects.create(user=self.user, digital_summary=summary, input_id=f"input-{index}", input_type="momentum", outcome="helpful")
        self.assertEqual(_feedback_preferences(self.user), {})
        third = self.create_summary(self.user)
        ActionableInputFeedback.objects.create(user=self.user, digital_summary=third, input_id="input-3", input_type="momentum", outcome="used")
        self.assertEqual(_feedback_preferences(self.user), {"momentum": 3})
        other = self.create_user("feedback-isolated")
        for index in range(3):
            summary = self.create_summary(other)
            ActionableInputFeedback.objects.create(user=other, digital_summary=summary, input_id=f"other-{index}", input_type="goal_target", outcome="helpful")
        self.assertNotIn("goal_target", _feedback_preferences(self.user))


class MobileIntegrationTests(MobileTestMixin, TestCase):
    def setUp(self):
        self.user = self.create_user()
        self.client.force_login(self.user)

    def test_health_endpoint_is_minimal_and_public(self):
        self.client.logout()
        response = self.client.get(reverse("core:health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertNotContains(response, "Django")

    def test_manual_advanced_submission_freezes_and_renders_assessment(self):
        self.create_goal(self.user)
        response = self.client.post(reverse("core:home"), {"screen_time": 180, "pickups": 40, "notifications": 70, "longest_session_minutes": 35, "mood": "Calm", "goal": "Study"})
        self.assertRedirects(response, reverse("core:summary"))
        summary = DigitalSummary.objects.get(user=self.user)
        self.assertEqual(summary.mobile_analytics_snapshot["pickups"], 40)
        self.assertEqual(summary.mobile_assessment_snapshot["assessment_version"], 1)
        page = self.client.get(reverse("core:summary"))
        for text in ("Mobile Analytics", "Usage Assessment", "Usage Signals", "Actionable Inputs", "Goal connection", "Goal Rescue"):
            self.assertContains(page, text)
        self.assertNotContains(page, "raw OCR")

    def test_blank_optional_fields_and_negative_validation(self):
        valid = PostcardForm({"screen_time": 60, "mood": "Calm", "goal": "Study"})
        self.assertTrue(valid.is_valid())
        invalid = PostcardForm({"screen_time": 60, "pickups": -1, "notifications": -2, "longest_session_minutes": -3, "mood": "Calm", "goal": "Study"})
        self.assertFalse(invalid.is_valid())

    def test_preferences_change_rendering_and_target_validation(self):
        goal, _ = self.create_goal(self.user)
        summary = self.create_summary(self.user, analytics={"source_type": "csv", "total_minutes": 200, "apps": [{"name": "Instagram", "minutes": 100}], "pickups": 40})
        self.freeze(summary)
        profile = self.user.userprofile
        profile.show_detailed_mobile_analytics = False
        profile.show_interaction_metrics = False
        profile.show_actionable_inputs = False
        profile.save()
        page = self.client.get(reverse("core:view_summary", args=[summary.id]))
        self.assertNotContains(page, "App usage breakdown")
        self.assertNotContains(page, "Device interaction metrics")
        self.assertNotContains(page, "Why this?")
        valid = UserProfileForm({"bio": "", "default_momentum_period": "all", "preferred_daily_screen_time_minutes": 240}, instance=profile)
        self.assertTrue(valid.is_valid())
        invalid = UserProfileForm({"bio": "", "default_momentum_period": "all", "preferred_daily_screen_time_minutes": 0}, instance=profile)
        self.assertFalse(invalid.is_valid())
        goal.delete()

    def test_historical_snapshot_legacy_and_xss(self):
        self.create_goal(self.user, title="<script>alert(1)</script>")
        summary = self.create_summary(self.user, analytics={"source_type": "csv", "total_minutes": 100, "apps": [{"name": "<img src=x onerror=alert(1)>", "minutes": 70}]})
        frozen = self.freeze(summary)
        page = self.client.get(reverse("core:view_summary", args=[summary.id]))
        self.assertNotContains(page, "<img src=x", html=False)
        self.assertContains(page, "&lt;img src=x onerror=alert(1)&gt;", html=False)
        summary.mobile_analytics_snapshot = {}
        summary.mobile_assessment_snapshot = {}
        summary.save(update_fields=["mobile_analytics_snapshot", "mobile_assessment_snapshot"])
        legacy_page = self.client.get(reverse("core:view_summary", args=[summary.id]))
        self.assertContains(legacy_page, "Historical assessment unavailable")
        self.assertNotEqual(frozen, {})

    def test_feedback_security_methods_idempotence_and_outcomes(self):
        summary = self.create_summary(self.user, assessment={"assessment_version": 1, "actionable_inputs": [{"id": "input-one", "type": "baseline"}]})
        url = reverse("core:actionable_input_feedback", args=[summary.id])
        self.assertEqual(self.client.get(url).status_code, 405)
        for outcome in ("helpful", "used", "not_useful"):
            response = self.client.post(url, {"input_id": "input-one", "outcome": outcome})
            self.assertRedirects(response, reverse("core:view_summary", args=[summary.id]))
            self.assertEqual(ActionableInputFeedback.objects.count(), 1)
            self.assertEqual(ActionableInputFeedback.objects.get().outcome, outcome)
        self.client.post(url, {"input_id": "missing", "outcome": "helpful"})
        self.assertEqual(ActionableInputFeedback.objects.count(), 1)
        other = self.create_user("feedback-other")
        other_summary = self.create_summary(other, assessment={"actionable_inputs": [{"id": "other", "type": "baseline"}]})
        self.assertEqual(self.client.post(reverse("core:actionable_input_feedback", args=[other_summary.id]), {"input_id": "other", "outcome": "helpful"}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(url, {"input_id": "input-one", "outcome": "helpful"}).status_code, 302)

    def test_history_and_personal_export_are_user_scoped(self):
        own = self.create_summary(self.user, analytics={"source_type": "manual", "total_minutes": 80, "apps": []}, assessment={"assessment_version": 1, "data_quality": "Manual only", "actionable_inputs": []})
        other = self.create_user("export-other")
        foreign = self.create_summary(other, total=999, analytics={"source_type": "manual", "total_minutes": 999, "apps": []}, assessment={"assessment_version": 1})
        self.assertEqual(self.client.get(reverse("core:view_summary", args=[foreign.id])).status_code, 404)
        payload = json.loads(self.client.get(reverse("core:export_personal_data")).content)
        self.assertEqual([item["id"] for item in payload["digital_summaries"]], [own.id])
        self.assertIn("mobile_analytics_snapshot", payload["digital_summaries"][0])
        self.assertIn("mobile_assessment_snapshot", payload["digital_summaries"][0])
        self.assertIn("show_actionable_inputs", payload["profile"]["preferences"])
        serialized = json.dumps(payload)
        for forbidden in ("password", "sessionid", "csrf", "SECRET_KEY", "raw_ocr"):
            self.assertNotIn(forbidden, serialized)

    def test_weekly_mobile_service_and_exports(self):
        now = timezone.localdate()
        summary = self.create_summary(self.user, total=120, analytics={"source_type": "ios_screen_time", "platform": "ios", "total_minutes": 120, "apps": [{"name": "Notion", "minutes": 60}], "pickups": 20, "notifications": 30}, assessment={"assessment_version": 1, "data_quality": "Complete", "actionable_inputs": [{"id": "goal-target", "type": "goal_target"}]})
        week_start = now - timedelta(days=now.weekday())
        result = build_weekly_mobile_analytics(self.user, week_start, week_start + timedelta(days=6))
        self.assertEqual(result["report_count"], 1)
        self.assertEqual(result["top_app"], "Notion")
        self.assertEqual(result["average_pickups"], 20)
        csv_response = self.client.get(reverse("core:weekly_review_csv"), {"week": week_start.isoformat()})
        self.assertEqual(csv_response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", csv_response["Content-Disposition"])
        csv_text = csv_response.content.decode("utf-8-sig")
        self.assertIn("Mobile Analytics", csv_text)
        self.assertIn("Notion", csv_text)
        self.assertNotIn("raw OCR", csv_text)
        pdf_response = self.client.get(reverse("core:weekly_review_pdf"), {"week": week_start.isoformat()})
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))
        self.assertIn("attachment", pdf_response["Content-Disposition"])
        self.assertGreater(summary.id, 0)

    def test_historical_week_mobile_data_has_no_current_contamination(self):
        historical = self.create_summary(self.user, total=100, days_ago=10, analytics={"source_type": "csv", "total_minutes": 100, "apps": [{"name": "Historical App", "minutes": 50}], "pickups": 10})
        self.create_summary(self.user, total=900, analytics={"source_type": "csv", "total_minutes": 900, "apps": [{"name": "Current App", "minutes": 800}], "pickups": 90})
        historical_day = timezone.localtime(historical.created_at).date()
        week_start = historical_day - timedelta(days=historical_day.weekday())
        result = build_weekly_mobile_analytics(self.user, week_start, week_start + timedelta(days=6))
        self.assertEqual(result["report_count"], 1)
        self.assertEqual(result["average_screen_time"], 100)
        self.assertEqual(result["top_app"], "Historical App")

    def test_mobile_insights_samples_exclude_nulls_and_users(self):
        other = self.create_user("insights-other")
        today = timezone.localdate()
        for days, pickups in ((5, 30), (10, 40), (35, 10), (40, 20)):
            self.create_summary(self.user, days_ago=days, analytics={"source_type": "csv", "total_minutes": 100, "apps": [{"name": "Notion", "minutes": 50, "category": "Productivity"}], "pickups": pickups, "notifications": pickups + 10, "longest_session_minutes": 20})
        self.create_summary(self.user, days_ago=2, analytics={"source_type": "manual", "total_minutes": 90, "apps": []})
        self.create_summary(other, days_ago=3, analytics={"source_type": "csv", "total_minutes": 999, "apps": [], "pickups": 999})
        result = build_mobile_insights(self.user, today=today)
        self.assertEqual(result["metrics"]["pickups"]["samples"], 2)
        self.assertEqual(result["metrics"]["pickups"]["direction"], "higher")
        self.assertEqual(result["category_distribution"][0]["category"], "Productivity")
        self.assertIn("csv", result["sources"])
        self.assertIn("Complete", result["data_quality"])

    def test_admin_frozen_fields_and_registration(self):
        self.assertIsInstance(admin.site._registry[ActionableInputFeedback], ActionableInputFeedbackAdmin)
        summary_admin = admin.site._registry[DigitalSummary]
        self.assertIn("mobile_analytics_snapshot", summary_admin.readonly_fields)
        self.assertIn("mobile_assessment_snapshot", summary_admin.readonly_fields)
        feedback_admin = admin.site._registry[ActionableInputFeedback]
        self.assertIn("outcome", feedback_admin.list_filter)
        self.assertIn("input_type", feedback_admin.search_fields)

    @override_settings(DEBUG=True)
    def test_demo_seed_mobile_data_is_repeatable(self):
        call_command("seed_demo_data", verbosity=0)
        first = DigitalSummary.objects.filter(user__username="beyondscreen_demo").count()
        call_command("seed_demo_data", verbosity=0)
        summaries = DigitalSummary.objects.filter(user__username="beyondscreen_demo")
        self.assertEqual(summaries.count(), first)
        self.assertTrue(summaries.filter(mobile_analytics_snapshot__platform="android").exists())
        self.assertTrue(summaries.filter(mobile_analytics_snapshot__platform="ios").exists())
        for summary in summaries:
            self.assertIn("pickups", summary.mobile_analytics_snapshot)
            self.assertIn("notifications", summary.mobile_analytics_snapshot)
            self.assertEqual(summary.mobile_assessment_snapshot["assessment_version"], 1)

    def test_anonymous_home_creates_transient_only(self):
        self.client.logout()
        response = self.client.post(reverse("core:home"), {"screen_time": 90, "mood": "Calm", "goal": "Study"}, follow=True)
        self.assertContains(response, "Personal baseline unavailable")
        self.assertContains(response, "Sign in and create Goal DNA")
        self.assertEqual(DigitalSummary.objects.count(), 0)

    def test_lightweight_end_to_end_mobile_flow(self):
        goal, _actions = self.create_goal(self.user)
        response = self.client.post(reverse("core:home"), {"screen_time": 240, "pickups": 55, "notifications": 80, "mood": "Calm", "goal": "Study"})
        self.assertRedirects(response, reverse("core:summary"))
        summary = DigitalSummary.objects.get(user=self.user)
        self.assertTrue(summary.mobile_assessment_snapshot["actionable_inputs"])
        rescue = summary.goal_rescue_snapshot
        self.assertEqual(rescue.get("status"), "ready")
        self.client.post(reverse("core:complete_goal_rescue"), {"summary_id": summary.id})
        self.assertTrue(MomentumEntry.objects.filter(user=self.user, digital_summary=summary).exists())
        self.assertEqual(self.client.get(reverse("core:goal_progress", args=[goal.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:weekly_review")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:insights")).status_code, 200)
        input_item = summary.mobile_assessment_snapshot["actionable_inputs"][0]
        self.client.post(reverse("core:actionable_input_feedback", args=[summary.id]), {"input_id": input_item["id"], "outcome": "helpful"})
        self.assertTrue(ActionableInputFeedback.objects.filter(user=self.user, digital_summary=summary).exists())
        self.assertEqual(self.client.get(reverse("core:view_summary", args=[summary.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:weekly_review_csv")).status_code, 200)
        self.assertTrue(self.client.get(reverse("core:weekly_review_pdf")).content.startswith(b"%PDF"))
        export = json.loads(self.client.get(reverse("core:export_personal_data")).content)
        self.assertEqual(export["digital_summaries"][0]["id"], summary.id)
        self.assertEqual(export["actionable_input_feedback"][0]["outcome"], "helpful")
        self.assertIn(rescue["action_title"], {item.action_title for item in MomentumEntry.objects.filter(user=self.user)})
