from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.conf import settings
from django.db import IntegrityError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import datetime, timedelta
from unittest.mock import patch
from io import StringIO

from .models import (
    DigitalSummary,
    GoalAction,
    GoalRescueOutcome,
    MomentumEntry,
    UserGoal,
)
from .services import (
    build_goal_health,
    build_goal_milestones,
    build_goal_outcome_analytics,
    build_goal_progress,
    build_goal_rescue,
    build_weekly_review,
    ensure_goal_rescue_outcome,
    freeze_goal_rescue_snapshot,
    generate_postcard,
    goal_rescue_for_summary,
)


class HomeViewTests(TestCase):
    def test_home_page_loads_all_available_choices(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Screen time today")
        self.assertContains(response, "Calm")
        self.assertContains(response, "Be more present")

    def test_valid_submission_creates_postcard_and_downloads(self):
        response = self.client.post(
            reverse("core:home"),
            {"screen_time": 245, "mood": "Tired", "goal": "Presence"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("core:summary"))
        response = self.client.get(response.url)
        self.assertContains(response, "4h 05m")
        self.assertIn("postcard", self.client.session)

        png_response = self.client.get(reverse("core:download_postcard", args=["png"]))
        self.assertEqual(png_response.status_code, 200)
        self.assertEqual(png_response["Content-Type"], "image/png")
        self.assertTrue(png_response.content.startswith(b"\x89PNG"))

        pdf_response = self.client.get(reverse("core:download_postcard", args=["pdf"]))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))

    def test_invalid_submission_shows_errors(self):
        response = self.client.post(reverse("core:home"), {"mood": "", "goal": ""})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")

    def test_unsupported_upload_type_shows_error(self):
        response = self.client.post(
            reverse("core:home"),
            {
                "file": SimpleUploadedFile("report.exe", b"not a report"),
                "mood": "Happy",
                "goal": "Study",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a CSV, PDF, PNG, JPG, or text file.")

    def test_download_requires_a_generated_postcard(self):
        response = self.client.get(reverse("core:download_postcard", args=["png"]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("core:home"))


class PostcardEngineTests(TestCase):
    def test_engine_has_required_content_variety(self):
        moods = ("Happy", "Calm", "Neutral", "Stressed", "Tired")
        goals = ("Study", "Fitness", "Better Sleep", "Productivity", "Presence")
        reflections = {
            generate_postcard(mood=mood, goal=goal, screen_time=None, has_report=has_report)["reflection"]
            for mood in moods
            for goal in goals
            for has_report in (False, True)
        }
        postcards = [
            generate_postcard(mood="Calm", goal="Study", screen_time=None, has_report=False)
            for _ in range(100)
        ]

        self.assertEqual(len(reflections), 50)
        self.assertEqual(len({postcard["haiku"] for postcard in postcards}), 100)
        self.assertEqual(len({postcard["action"] for postcard in postcards}), 100)
        self.assertEqual(len({postcard["pledge"] for postcard in postcards}), 100)


class PrimaryGoalSwitchingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="switcher",
            password="test-password",
        )
        self.other_user = User.objects.create_user(
            username="other-switcher",
            password="test-password",
        )
        self.primary = self.create_goal(
            self.user,
            "Write a novel",
            is_primary=True,
        )
        self.target = self.create_goal(
            self.user,
            "Run a marathon",
        )
        self.client.force_login(self.user)

    def create_goal(
        self,
        user,
        title,
        *,
        is_primary=False,
        status=UserGoal.STATUS_ACTIVE,
        with_actions=True,
    ):
        goal = UserGoal.objects.create(
            user=user,
            title=title,
            why_it_matters=f"Why {title} matters",
            current_focus=f"Focus for {title}",
            progress_unit="sessions",
            weekly_target=3,
            is_primary=is_primary,
            status=status,
            preferred_days=["monday"],
        )
        if with_actions:
            for size, minutes in (
                (GoalAction.SIZE_MINIMUM, 5),
                (GoalAction.SIZE_STANDARD, 20),
                (GoalAction.SIZE_DEEP, 45),
            ):
                GoalAction.objects.create(
                    goal=goal,
                    size=size,
                    title=f"{title} {size}",
                    duration_minutes=minutes,
                    progress_value=1,
                )
        return goal

    def switch_url(self, goal=None):
        return reverse(
            "core:make_primary_goal",
            args=[(goal or self.target).pk],
        )

    def assert_original_primary_state(self):
        self.primary.refresh_from_db()
        self.target.refresh_from_db()
        self.assertTrue(self.primary.is_primary)
        self.assertEqual(self.primary.status, UserGoal.STATUS_ACTIVE)
        self.assertFalse(self.target.is_primary)

    def test_promotes_active_additional_goal_and_keeps_exactly_one_primary(self):
        response = self.client.post(self.switch_url())

        self.assertRedirects(response, reverse("core:goal_dna_management"))
        self.primary.refresh_from_db()
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_primary)
        self.assertFalse(self.primary.is_primary)
        self.assertEqual(self.primary.status, UserGoal.STATUS_ACTIVE)
        self.assertEqual(
            UserGoal.objects.filter(
                user=self.user,
                status=UserGoal.STATUS_ACTIVE,
                is_primary=True,
            ).count(),
            1,
        )

    def test_switch_with_three_active_goals_preserves_all_three(self):
        third = self.create_goal(self.user, "Learn Spanish")

        self.client.post(self.switch_url())

        active_goals = UserGoal.objects.filter(
            user=self.user,
            status=UserGoal.STATUS_ACTIVE,
        )
        self.assertEqual(active_goals.count(), 3)
        self.assertSetEqual(
            set(active_goals.values_list("pk", flat=True)),
            {self.primary.pk, self.target.pk, third.pk},
        )

    def test_paused_target_is_rejected_without_changes(self):
        self.target.status = UserGoal.STATUS_PAUSED
        self.target.save(update_fields=["status"])

        response = self.client.post(self.switch_url())

        self.assertEqual(response.status_code, 404)
        self.assert_original_primary_state()

    def test_completed_target_is_rejected_without_changes(self):
        self.target.status = UserGoal.STATUS_COMPLETED
        self.target.save(update_fields=["status"])

        response = self.client.post(self.switch_url())

        self.assertEqual(response.status_code, 404)
        self.assert_original_primary_state()

    def test_another_users_goal_is_rejected_without_changes(self):
        other_goal = self.create_goal(self.other_user, "Private goal")

        response = self.client.post(self.switch_url(other_goal))

        self.assertEqual(response.status_code, 404)
        self.assert_original_primary_state()

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.switch_url()).status_code, 405)
        self.assert_original_primary_state()

    def test_anonymous_post_redirects_to_login(self):
        self.client.logout()

        response = self.client.post(self.switch_url())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{settings.LOGIN_URL}?next={self.switch_url()}",
        )
        self.assert_original_primary_state()

    def test_selecting_current_primary_is_a_no_op(self):
        before = {
            goal.pk: (goal.is_primary, goal.status, goal.updated_at)
            for goal in UserGoal.objects.filter(user=self.user)
        }

        response = self.client.post(self.switch_url(self.primary))

        self.assertRedirects(response, reverse("core:goal_dna_management"))
        after = {
            goal.pk: (goal.is_primary, goal.status, goal.updated_at)
            for goal in UserGoal.objects.filter(user=self.user)
        }
        self.assertEqual(after, before)

    def test_incomplete_action_ladder_is_rejected_without_partial_updates(self):
        self.target.actions.filter(size=GoalAction.SIZE_DEEP).delete()

        response = self.client.post(self.switch_url())

        self.assertRedirects(response, reverse("core:goal_dna_management"))
        self.assert_original_primary_state()


    def test_goal_rescue_immediately_uses_promoted_goal(self):
        self.client.post(self.switch_url())

        rescue = build_goal_rescue(self.user, 200)

        self.assertEqual(rescue["status"], "ready")
        self.assertEqual(rescue["goal_title"], self.target.title)
        self.assertIn(
            rescue["action_id"],
            self.target.actions.values_list("pk", flat=True),
        )

    def test_momentum_entries_and_goal_dna_row_identities_are_preserved(self):
        summary = DigitalSummary.objects.create(
            user=self.user,
            screen_time_minutes=120,
            wellness_score=70,
            category="Balanced",
            insight="Existing insight",
        )
        primary_action = self.primary.actions.get(size=GoalAction.SIZE_MINIMUM)
        entry = MomentumEntry.objects.create(
            user=self.user,
            goal=self.primary,
            action=primary_action,
            digital_summary=summary,
            action_title=primary_action.title,
            action_size=primary_action.size,
            duration_minutes=primary_action.duration_minutes,
            progress_value=primary_action.progress_value,
            progress_unit=self.primary.progress_unit,
        )
        goal_ids = set(UserGoal.objects.values_list("pk", flat=True))
        action_ids = set(GoalAction.objects.values_list("pk", flat=True))
        entry_snapshot = tuple(
            MomentumEntry.objects.filter(pk=entry.pk).values_list(
                "pk", "goal_id", "action_id", "action_title",
                "action_size", "duration_minutes", "progress_value",
                "progress_unit",
            ).get()
        )

        self.client.post(self.switch_url())

        self.assertSetEqual(
            set(UserGoal.objects.values_list("pk", flat=True)),
            goal_ids,
        )
        self.assertSetEqual(
            set(GoalAction.objects.values_list("pk", flat=True)),
            action_ids,
        )
        self.assertEqual(
            tuple(
                MomentumEntry.objects.filter(pk=entry.pk).values_list(
                    "pk", "goal_id", "action_id", "action_title",
                    "action_size", "duration_minutes", "progress_value",
                    "progress_unit",
                ).get()
            ),
            entry_snapshot,
        )

    def test_management_page_renders_promoted_and_former_goal_in_new_roles(self):
        self.client.post(self.switch_url())

        response = self.client.get(reverse("core:goal_dna_management"))

        self.assertEqual(response.context["primary_goal"]["id"], self.target.pk)
        self.assertEqual(
            [goal["id"] for goal in response.context["additional_goals"]],
            [self.primary.pk],
        )

    def test_dashboard_context_reflects_promoted_primary(self):
        self.client.post(self.switch_url())

        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(
            response.context["momentum_summary"]["primary_goal_title"],
            self.target.title,
        )
        self.assertEqual(
            response.context["dashboard_summary"]["goal_rescue"]["goal_title"],
            self.target.title,
        )

    def test_url_reversal_and_management_form_use_post_with_csrf(self):
        self.assertEqual(
            self.switch_url(),
            f"/goals/{self.target.pk}/make-primary/",
        )

        response = self.client.get(reverse("core:goal_dna_management"))

        self.assertContains(response, 'method="post"')
        self.assertContains(response, f'action="{self.switch_url()}"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_integrity_error_rolls_back_both_primary_updates(self):
        original_save = UserGoal.save

        def fail_target_save(goal, *args, **kwargs):
            if goal.pk == self.target.pk and goal.is_primary:
                raise IntegrityError("simulated competing switch")
            return original_save(goal, *args, **kwargs)

        with patch.object(UserGoal, "save", new=fail_target_save):
            response = self.client.post(self.switch_url())

        self.assertRedirects(response, reverse("core:goal_dna_management"))
        self.assert_original_primary_state()


class AdditionalGoalManagementPhase2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="additional-manager",
            password="test-password",
        )
        self.other_user = User.objects.create_user(
            username="additional-outsider",
            password="test-password",
        )
        self.primary = self.create_goal(
            self.user,
            "Primary writing goal",
            is_primary=True,
        )
        self.additional = self.create_goal(
            self.user,
            "Additional fitness goal",
        )
        self.client.force_login(self.user)

    def create_goal(
        self,
        user,
        title,
        *,
        is_primary=False,
        status=UserGoal.STATUS_ACTIVE,
    ):
        goal = UserGoal.objects.create(
            user=user,
            title=title,
            why_it_matters=f"A meaningful reason for {title}",
            current_focus="Original focus",
            progress_unit="sessions",
            weekly_target=3,
            is_primary=is_primary,
            status=status,
            preferred_days=["monday"],
        )
        for size, minutes in (
            (GoalAction.SIZE_MINIMUM, 5),
            (GoalAction.SIZE_STANDARD, 20),
            (GoalAction.SIZE_DEEP, 45),
        ):
            GoalAction.objects.create(
                goal=goal,
                size=size,
                title=f"{title} {size}",
                duration_minutes=minutes,
                progress_value=1,
            )
        return goal

    def edit_payload(self):
        return {
            "title": "Updated additional fitness goal",
            "why_it_matters": "This updated reason remains meaningful",
            "current_focus": "Updated focus",
            "progress_unit": "sessions",
            "weekly_target": 4,
            "preferred_days": ["tuesday", "thursday"],
            "preferred_time": "08:30",
            "deadline": "",
            "minimum_action_title": "Updated minimum action",
            "minimum_action_minutes": 10,
            "minimum_action_progress": "",
            "standard_action_title": "Updated standard action",
            "standard_action_minutes": 25,
            "standard_action_progress": "",
            "deep_action_title": "Updated deep action",
            "deep_action_minutes": 60,
            "deep_action_progress": "",
        }

    def url(self, name, goal=None):
        return reverse(
            f"core:{name}",
            args=[(goal or self.additional).pk],
        )

    def assert_primary_unchanged(self):
        self.primary.refresh_from_db()
        self.assertTrue(self.primary.is_primary)
        self.assertEqual(self.primary.status, UserGoal.STATUS_ACTIVE)
        self.assertEqual(
            UserGoal.objects.get(
                user=self.user,
                status=UserGoal.STATUS_ACTIVE,
                is_primary=True,
            ).pk,
            self.primary.pk,
        )

    def test_edit_active_additional_goal_preserves_roles_and_identities(self):
        goal_pk = self.additional.pk
        action_pks = set(
            self.additional.actions.values_list("pk", flat=True)
        )

        response = self.client.post(
            self.url("additional_goal_edit"),
            self.edit_payload(),
        )

        self.assertRedirects(response, reverse("core:goal_dna_management"))
        self.additional.refresh_from_db()
        self.assertEqual(self.additional.pk, goal_pk)
        self.assertEqual(self.additional.title, "Updated additional fitness goal")
        self.assertEqual(self.additional.current_focus, "Updated focus")
        self.assertFalse(self.additional.is_primary)
        self.assertSetEqual(
            set(self.additional.actions.values_list("pk", flat=True)),
            action_pks,
        )
        self.assertEqual(
            self.additional.actions.get(size=GoalAction.SIZE_MINIMUM).title,
            "Updated minimum action",
        )
        self.assert_primary_unchanged()

    def test_pause_additional_goal_reduces_active_count(self):
        before_count = UserGoal.objects.filter(
            user=self.user,
            status=UserGoal.STATUS_ACTIVE,
        ).count()

        response = self.client.post(self.url("pause_additional_goal"))

        self.assertRedirects(response, reverse("core:goal_dna_management"))
        self.additional.refresh_from_db()
        self.assertEqual(self.additional.status, UserGoal.STATUS_PAUSED)
        self.assertFalse(self.additional.is_primary)
        self.assertEqual(
            UserGoal.objects.filter(
                user=self.user,
                status=UserGoal.STATUS_ACTIVE,
            ).count(),
            before_count - 1,
        )
        self.assert_primary_unchanged()

    def test_resume_paused_additional_goal_remains_non_primary(self):
        self.additional.status = UserGoal.STATUS_PAUSED
        self.additional.save(update_fields=["status"])

        response = self.client.post(self.url("resume_additional_goal"))

        self.assertRedirects(response, reverse("core:goal_dna_management"))
        self.additional.refresh_from_db()
        self.assertEqual(self.additional.status, UserGoal.STATUS_ACTIVE)
        self.assertFalse(self.additional.is_primary)
        self.assert_primary_unchanged()

    def test_resume_is_rejected_when_three_goals_are_active(self):
        self.additional.status = UserGoal.STATUS_PAUSED
        self.additional.save(update_fields=["status"])
        self.create_goal(self.user, "Second active additional")
        self.create_goal(self.user, "Third active additional")

        response = self.client.post(
            self.url("resume_additional_goal"),
            follow=True,
        )

        self.additional.refresh_from_db()
        self.assertEqual(self.additional.status, UserGoal.STATUS_PAUSED)
        self.assertFalse(self.additional.is_primary)
        self.assertContains(response, "You already have three active goals")
        self.assertEqual(
            UserGoal.objects.filter(
                user=self.user,
                status=UserGoal.STATUS_ACTIVE,
            ).count(),
            3,
        )

    def test_complete_additional_goal_and_reject_resume(self):
        response = self.client.post(self.url("complete_additional_goal"))

        self.assertRedirects(response, reverse("core:goal_dna_management"))
        self.additional.refresh_from_db()
        self.assertEqual(self.additional.status, UserGoal.STATUS_COMPLETED)
        self.assertFalse(self.additional.is_primary)
        self.assert_primary_unchanged()

        resume_response = self.client.post(self.url("resume_additional_goal"))
        self.assertEqual(resume_response.status_code, 404)
        self.additional.refresh_from_db()
        self.assertEqual(self.additional.status, UserGoal.STATUS_COMPLETED)

    def test_another_users_additional_goal_cannot_be_accessed_or_changed(self):
        other_goal = self.create_goal(self.other_user, "Other private goal")

        self.assertEqual(
            self.client.get(self.url("additional_goal_edit", other_goal)).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                self.url("additional_goal_edit", other_goal),
                self.edit_payload(),
            ).status_code,
            404,
        )
        for name in (
            "pause_additional_goal",
            "resume_additional_goal",
            "complete_additional_goal",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    self.client.post(self.url(name, other_goal)).status_code,
                    404,
                )
        other_goal.refresh_from_db()
        self.assertEqual(other_goal.status, UserGoal.STATUS_ACTIVE)
        self.assertFalse(other_goal.is_primary)

    def test_lifecycle_get_requests_are_rejected(self):
        for name in (
            "pause_additional_goal",
            "resume_additional_goal",
            "complete_additional_goal",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.url(name)).status_code, 405)

    def test_anonymous_users_are_redirected_to_login(self):
        self.client.logout()
        names = (
            "additional_goal_edit",
            "pause_additional_goal",
            "resume_additional_goal",
            "complete_additional_goal",
        )
        for name in names:
            with self.subTest(name=name):
                response = self.client.post(self.url(name))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(settings.LOGIN_URL))

    def test_goal_rescue_keeps_using_primary_through_all_additional_states(self):
        expected_action_ids = set(
            self.primary.actions.values_list("pk", flat=True)
        )

        def assert_rescue_primary():
            rescue = build_goal_rescue(self.user, 200)
            self.assertEqual(rescue["goal_title"], self.primary.title)
            self.assertIn(rescue["action_id"], expected_action_ids)

        self.client.post(
            self.url("additional_goal_edit"),
            self.edit_payload(),
        )
        assert_rescue_primary()
        self.client.post(self.url("pause_additional_goal"))
        assert_rescue_primary()
        self.client.post(self.url("resume_additional_goal"))
        assert_rescue_primary()
        self.client.post(self.url("complete_additional_goal"))
        assert_rescue_primary()

    def test_momentum_entry_and_goal_dna_identities_remain_unchanged(self):
        summary = DigitalSummary.objects.create(
            user=self.user,
            screen_time_minutes=90,
            wellness_score=65,
            category="Balanced",
            insight="Historical insight",
        )
        action = self.additional.actions.get(size=GoalAction.SIZE_MINIMUM)
        entry = MomentumEntry.objects.create(
            user=self.user,
            goal=self.additional,
            action=action,
            digital_summary=summary,
            action_title=action.title,
            action_size=action.size,
            duration_minutes=action.duration_minutes,
            progress_value=action.progress_value,
            progress_unit=self.additional.progress_unit,
        )
        goal_ids = set(UserGoal.objects.values_list("pk", flat=True))
        action_ids = set(GoalAction.objects.values_list("pk", flat=True))
        entry_snapshot = MomentumEntry.objects.filter(pk=entry.pk).values().get()

        self.client.post(
            self.url("additional_goal_edit"),
            self.edit_payload(),
        )
        self.client.post(self.url("pause_additional_goal"))
        self.client.post(self.url("resume_additional_goal"))
        self.client.post(self.url("complete_additional_goal"))

        self.assertSetEqual(
            set(UserGoal.objects.values_list("pk", flat=True)),
            goal_ids,
        )
        self.assertSetEqual(
            set(GoalAction.objects.values_list("pk", flat=True)),
            action_ids,
        )
        self.assertEqual(
            MomentumEntry.objects.filter(pk=entry.pk).values().get(),
            entry_snapshot,
        )

    def test_management_page_shows_active_additional_controls(self):
        response = self.client.get(reverse("core:goal_dna_management"))

        self.assertContains(response, self.url("additional_goal_edit"))
        self.assertContains(response, self.url("pause_additional_goal"))
        self.assertContains(response, self.url("complete_additional_goal"))
        self.assertContains(
            response,
            reverse("core:make_primary_goal", args=[self.additional.pk]),
        )

    def test_management_page_shows_paused_additional_resume_control(self):
        self.client.post(self.url("pause_additional_goal"))

        response = self.client.get(reverse("core:goal_dna_management"))

        self.assertContains(response, "PAUSED ADDITIONAL GOAL")
        self.assertContains(response, self.url("resume_additional_goal"))
        self.assertNotContains(response, self.url("additional_goal_edit"))

    def test_management_page_shows_completed_without_resume_or_make_primary(self):
        self.client.post(self.url("complete_additional_goal"))

        response = self.client.get(reverse("core:goal_dna_management"))

        self.assertContains(response, "COMPLETED ADDITIONAL GOAL")
        self.assertNotContains(response, self.url("resume_additional_goal"))
        self.assertNotContains(
            response,
            reverse("core:make_primary_goal", args=[self.additional.pk]),
        )


class HistoricalGoalRescueStabilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="snapshot-user",
            password="test-password",
        )
        self.primary = self.create_goal(
            "Original primary",
            is_primary=True,
        )
        self.additional = self.create_goal("Future primary")
        self.client.force_login(self.user)

    def create_goal(self, title, *, is_primary=False):
        goal = UserGoal.objects.create(
            user=self.user,
            title=title,
            why_it_matters=f"Why {title} matters",
            current_focus=f"Focus for {title}",
            progress_unit="sessions",
            weekly_target=3,
            is_primary=is_primary,
            status=UserGoal.STATUS_ACTIVE,
            preferred_days=["monday"],
        )
        for size, minutes in (
            (GoalAction.SIZE_MINIMUM, 5),
            (GoalAction.SIZE_STANDARD, 20),
            (GoalAction.SIZE_DEEP, 45),
        ):
            GoalAction.objects.create(
                goal=goal,
                size=size,
                title=f"{title} {size}",
                duration_minutes=minutes,
                progress_value=1,
            )
        return goal

    def create_summary(self, minutes=200, *, snapshot=True):
        rescue_snapshot = None
        if snapshot:
            rescue_snapshot = freeze_goal_rescue_snapshot(
                build_goal_rescue(self.user, minutes)
            )
        return DigitalSummary.objects.create(
            user=self.user,
            screen_time_minutes=minutes,
            wellness_score=70,
            category="Balanced",
            insight="Snapshot insight",
            goal_rescue_snapshot=rescue_snapshot,
        )

    def test_new_summary_created_by_home_stores_goal_rescue_snapshot(self):
        response = self.client.post(
            reverse("core:home"),
            {"screen_time": 200, "mood": "Calm", "goal": "Study"},
        )

        self.assertEqual(response.status_code, 302)
        summary = DigitalSummary.objects.get(user=self.user)
        snapshot = summary.goal_rescue_snapshot
        selected_action = self.primary.actions.get(pk=snapshot["action_id"])
        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["goal_id"], self.primary.pk)
        self.assertEqual(snapshot["goal_title"], self.primary.title)
        self.assertEqual(snapshot["action_title"], selected_action.title)
        self.assertEqual(snapshot["action_size"], selected_action.size)
        self.assertEqual(snapshot["action_minutes"], selected_action.duration_minutes)
        self.assertEqual(snapshot["action_progress_value"], "1.00")
        self.assertEqual(snapshot["progress_unit"], self.primary.progress_unit)

    def test_historical_views_keep_snapshot_after_primary_switch(self):
        summary = self.create_summary()
        original = summary.goal_rescue_snapshot.copy()
        session = self.client.session
        session["summary_data"] = {
            "summary_id": summary.pk,
            "screen_time_minutes": summary.screen_time_minutes,
            "insight": summary.insight,
        }
        session.save()

        self.client.post(
            reverse("core:make_primary_goal", args=[self.additional.pk])
        )

        current_response = self.client.get(reverse("core:summary"))
        detail_response = self.client.get(
            reverse("core:view_summary", args=[summary.pk])
        )
        history_response = self.client.get(reverse("core:history"))
        self.assertEqual(
            current_response.context["summary"]["goal_rescue"]["goal_title"],
            original["goal_title"],
        )
        self.assertEqual(
            detail_response.context["goal_rescue"]["action_title"],
            original["action_title"],
        )
        self.assertEqual(
            history_response.context["history_reports"][0]["goal_rescue"],
            original,
        )

    def test_snapshot_survives_original_goal_and_action_edits(self):
        summary = self.create_summary()
        original = summary.goal_rescue_snapshot.copy()
        selected_action = GoalAction.objects.get(pk=original["action_id"])

        self.primary.title = "Edited primary title"
        self.primary.progress_unit = "tasks"
        self.primary.save(update_fields=["title", "progress_unit"])
        selected_action.title = "Edited action title"
        selected_action.duration_minutes = 99
        selected_action.progress_value = 7
        selected_action.save()

        summary.refresh_from_db()
        self.assertEqual(goal_rescue_for_summary(summary), original)

    def test_snapshot_survives_pausing_original_primary(self):
        summary = self.create_summary()
        original = summary.goal_rescue_snapshot.copy()

        self.client.post(
            reverse("core:pause_primary_goal", args=[self.primary.pk])
        )

        summary.refresh_from_db()
        self.assertEqual(goal_rescue_for_summary(summary), original)

    def test_snapshot_survives_completing_original_primary(self):
        summary = self.create_summary()
        original = summary.goal_rescue_snapshot.copy()

        self.client.post(
            reverse("core:complete_primary_goal", args=[self.primary.pk])
        )

        summary.refresh_from_db()
        self.assertEqual(goal_rescue_for_summary(summary), original)

    def test_old_rescue_completion_uses_frozen_values_and_prevents_duplicates(self):
        summary = self.create_summary()
        snapshot = summary.goal_rescue_snapshot.copy()
        original_action = GoalAction.objects.get(pk=snapshot["action_id"])

        self.client.post(
            reverse("core:make_primary_goal", args=[self.additional.pk])
        )
        original_action.title = "Later edited action"
        original_action.duration_minutes = 120
        original_action.progress_value = 9
        original_action.save()

        first = self.client.post(
            reverse("core:complete_goal_rescue"),
            {"summary_id": summary.pk},
        )
        second = self.client.post(
            reverse("core:complete_goal_rescue"),
            {"summary_id": summary.pk},
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(first.url, reverse("core:summary"))
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second.url, reverse("core:summary"))
        self.assertEqual(
            MomentumEntry.objects.filter(digital_summary=summary).count(),
            1,
        )
        entry = MomentumEntry.objects.get(digital_summary=summary)
        self.assertEqual(entry.goal_id, self.primary.pk)
        self.assertEqual(entry.action_id, original_action.pk)
        self.assertEqual(entry.action_title, snapshot["action_title"])
        self.assertEqual(entry.action_size, snapshot["action_size"])
        self.assertEqual(entry.duration_minutes, snapshot["action_minutes"])
        self.assertEqual(str(entry.progress_value), snapshot["action_progress_value"])
        self.assertEqual(entry.progress_unit, snapshot["progress_unit"])

    def test_new_summary_after_switch_uses_new_primary(self):
        old_summary = self.create_summary()
        self.client.post(
            reverse("core:make_primary_goal", args=[self.additional.pk])
        )

        new_summary = self.create_summary()

        self.assertEqual(
            old_summary.goal_rescue_snapshot["goal_id"],
            self.primary.pk,
        )
        self.assertEqual(
            new_summary.goal_rescue_snapshot["goal_id"],
            self.additional.pk,
        )

    def test_no_rescue_snapshot_does_not_gain_rescue_later(self):
        no_goal_user = User.objects.create_user(
            username="no-goal-snapshot",
            password="test-password",
        )
        self.client.force_login(no_goal_user)
        snapshot = freeze_goal_rescue_snapshot(
            build_goal_rescue(no_goal_user, 120)
        )
        summary = DigitalSummary.objects.create(
            user=no_goal_user,
            screen_time_minutes=120,
            wellness_score=70,
            category="Balanced",
            insight="No goal then",
            goal_rescue_snapshot=snapshot,
        )
        UserGoal.objects.create(
            user=no_goal_user,
            title="Later goal",
            why_it_matters="Created after the report",
            progress_unit="sessions",
            weekly_target=3,
            is_primary=True,
        )

        summary.refresh_from_db()
        self.assertEqual(goal_rescue_for_summary(summary)["status"], "no_goal")

    def test_zero_screen_time_state_is_frozen(self):
        summary = self.create_summary(minutes=0)
        self.assertEqual(
            summary.goal_rescue_snapshot["status"],
            "no_screen_time",
        )

        self.client.post(
            reverse("core:make_primary_goal", args=[self.additional.pk])
        )

        summary.refresh_from_db()
        self.assertEqual(
            goal_rescue_for_summary(summary)["status"],
            "no_screen_time",
        )
        self.assertEqual(
            goal_rescue_for_summary(summary)["goal_title"],
            self.primary.title,
        )

    def test_smallest_action_fallback_is_frozen_when_slice_fits_nothing(self):
        summary = self.create_summary(minutes=1)
        minimum_action = self.primary.actions.get(
            size=GoalAction.SIZE_MINIMUM,
        )

        self.assertEqual(
            summary.goal_rescue_snapshot["action_id"],
            minimum_action.pk,
        )

        self.client.post(
            reverse("core:make_primary_goal", args=[self.additional.pk])
        )
        minimum_action.title = "Changed after recommendation"
        minimum_action.save(update_fields=["title"])

        summary.refresh_from_db()
        rescue = goal_rescue_for_summary(summary)
        self.assertEqual(rescue["action_id"], minimum_action.pk)
        self.assertNotEqual(rescue["action_title"], minimum_action.title)

    def test_legacy_summary_is_explicitly_unavailable_and_not_completable(self):
        legacy = self.create_summary(snapshot=False)

        detail_response = self.client.get(
            reverse("core:view_summary", args=[legacy.pk])
        )
        completion_response = self.client.post(
            reverse("core:complete_goal_rescue"),
            {"summary_id": legacy.pk},
            follow=True,
        )

        self.assertEqual(
            detail_response.context["goal_rescue"]["status"],
            "legacy_unavailable",
        )
        self.assertContains(
            detail_response,
            "No historical recommendation was saved.",
        )
        self.assertContains(completion_response, "predates saved Goal Rescue")
        self.assertFalse(
            MomentumEntry.objects.filter(digital_summary=legacy).exists()
        )


class GoalSpecificProgressTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="progress-owner",
            password="test-password",
        )
        self.other_user = User.objects.create_user(
            username="progress-outsider",
            password="test-password",
        )
        self.primary = self.create_goal("Primary progress", is_primary=True)
        self.additional = self.create_goal("Additional progress")
        self.client.force_login(self.user)

    def create_goal(self, title, *, is_primary=False, status=UserGoal.STATUS_ACTIVE):
        goal = UserGoal.objects.create(
            user=self.user,
            title=title,
            why_it_matters=f"Why {title} matters",
            current_focus="Current focus",
            progress_unit="sessions",
            weekly_target=4,
            is_primary=is_primary,
            status=status,
            preferred_days=["monday"],
        )
        for size, minutes in (
            (GoalAction.SIZE_MINIMUM, 5),
            (GoalAction.SIZE_STANDARD, 20),
            (GoalAction.SIZE_DEEP, 45),
        ):
            GoalAction.objects.create(
                goal=goal,
                size=size,
                title=f"{title} {size}",
                duration_minutes=minutes,
                progress_value=1,
            )
        return goal

    def create_entry(
        self,
        goal,
        *,
        minutes=10,
        progress="1.00",
        completed_at=None,
        unit=None,
        title=None,
    ):
        summary = DigitalSummary.objects.create(
            user=self.user,
            screen_time_minutes=120,
            wellness_score=70,
            category="Balanced",
            insight="Progress source",
            goal_rescue_snapshot={"status": "ready"},
        )
        action = goal.actions.get(size=GoalAction.SIZE_MINIMUM)
        entry = MomentumEntry.objects.create(
            user=self.user,
            goal=goal,
            action=action,
            digital_summary=summary,
            action_title=title or action.title,
            action_size=action.size,
            duration_minutes=minutes,
            progress_value=progress,
            progress_unit=unit or goal.progress_unit,
        )
        if completed_at is not None:
            MomentumEntry.objects.filter(pk=entry.pk).update(
                completed_at=completed_at,
            )
            entry.refresh_from_db()
        return entry

    def progress_for(self, goal):
        entries = MomentumEntry.objects.filter(
            user=self.user,
            goal=goal,
        )
        return build_goal_progress(goal, entries)

    def test_goal_without_momentum_has_zero_empty_progress(self):
        progress = self.progress_for(self.primary)

        self.assertFalse(progress["has_activity"])
        self.assertEqual(progress["total_completed_actions"], 0)
        self.assertEqual(progress["total_reclaimed_minutes"], 0)
        self.assertEqual(progress["lifetime_progress_value"], 0)
        self.assertEqual(progress["current_week_progress"], 0)
        self.assertIsNone(progress["last_completed_at"])

    def test_lifetime_weekly_minutes_count_and_percentage_are_correct(self):
        now = timezone.now()
        self.create_entry(
            self.primary,
            minutes=15,
            progress="1.00",
            completed_at=now - timedelta(days=10),
        )
        recent = self.create_entry(
            self.primary,
            minutes=20,
            progress="2.00",
            completed_at=now,
        )

        progress = self.progress_for(self.primary)

        self.assertEqual(progress["total_completed_actions"], 2)
        self.assertEqual(progress["total_reclaimed_minutes"], 35)
        self.assertEqual(progress["lifetime_progress_value"], 3)
        self.assertEqual(progress["current_week_progress"], 2)
        self.assertEqual(progress["weekly_target"], 4)
        self.assertEqual(progress["weekly_percent"], 50)
        self.assertEqual(progress["last_completed_at"], recent.completed_at)

    def test_percentage_handles_zero_target_safely(self):
        self.primary.weekly_target = 0
        progress = build_goal_progress(self.primary, [])
        self.assertEqual(progress["weekly_percent"], 0)

    def test_different_goals_and_units_never_mix_progress(self):
        self.create_entry(self.primary, progress="2.00", minutes=10)
        self.create_entry(self.additional, progress="3.00", minutes=30)
        self.create_entry(
            self.primary,
            progress="60.00",
            minutes=60,
            unit="minutes",
        )

        primary_progress = self.progress_for(self.primary)
        additional_progress = self.progress_for(self.additional)

        self.assertEqual(primary_progress["lifetime_progress_value"], 2)
        self.assertEqual(primary_progress["total_completed_actions"], 2)
        self.assertEqual(primary_progress["total_reclaimed_minutes"], 70)
        self.assertTrue(primary_progress["has_mixed_units"])
        self.assertEqual(additional_progress["lifetime_progress_value"], 3)
        self.assertEqual(additional_progress["total_completed_actions"], 1)
        self.assertEqual(additional_progress["total_reclaimed_minutes"], 30)

    def test_switch_keeps_old_progress_and_future_rescue_uses_new_goal(self):
        self.create_entry(self.primary, progress="2.00")

        self.client.post(
            reverse("core:make_primary_goal", args=[self.additional.pk])
        )

        self.assertEqual(
            self.progress_for(self.primary)["lifetime_progress_value"],
            2,
        )
        self.assertEqual(
            self.progress_for(self.additional)["lifetime_progress_value"],
            0,
        )

        rescue = freeze_goal_rescue_snapshot(
            build_goal_rescue(self.user, 200)
        )
        summary = DigitalSummary.objects.create(
            user=self.user,
            screen_time_minutes=200,
            wellness_score=70,
            category="Balanced",
            insight="After switch",
            goal_rescue_snapshot=rescue,
        )
        self.client.post(
            reverse("core:complete_goal_rescue"),
            {"summary_id": summary.pk},
        )

        entry = MomentumEntry.objects.get(digital_summary=summary)
        self.assertEqual(entry.goal_id, self.additional.pk)
        self.assertEqual(
            self.progress_for(self.primary)["total_completed_actions"],
            1,
        )
        self.assertEqual(
            self.progress_for(self.additional)["total_completed_actions"],
            1,
        )

    def test_edit_pause_and_complete_do_not_erase_progress(self):
        self.create_entry(self.additional, progress="2.00", minutes=25)
        self.additional.title = "Edited additional title"
        self.additional.save(update_fields=["title"])
        self.client.post(
            reverse("core:pause_additional_goal", args=[self.additional.pk])
        )

        paused_progress = self.progress_for(self.additional)
        self.assertEqual(paused_progress["lifetime_progress_value"], 2)
        self.assertEqual(paused_progress["total_reclaimed_minutes"], 25)

        self.client.post(
            reverse("core:resume_additional_goal", args=[self.additional.pk])
        )
        self.client.post(
            reverse("core:complete_additional_goal", args=[self.additional.pk])
        )

        completed_progress = self.progress_for(self.additional)
        self.assertEqual(completed_progress["lifetime_progress_value"], 2)
        self.assertEqual(completed_progress["total_completed_actions"], 1)

    def test_progress_page_is_user_scoped_and_requires_login(self):
        other_goal = UserGoal.objects.create(
            user=self.other_user,
            title="Private progress",
            why_it_matters="Private reason",
            progress_unit="sessions",
            weekly_target=2,
        )

        self.assertEqual(
            self.client.get(
                reverse("core:goal_progress", args=[other_goal.pk])
            ).status_code,
            404,
        )

        self.client.logout()
        response = self.client.get(
            reverse("core:goal_progress", args=[self.primary.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))

    def test_management_renders_primary_and_additional_progress(self):
        self.create_entry(self.primary, progress="2.00", minutes=20)
        self.create_entry(self.additional, progress="1.00", minutes=10)

        response = self.client.get(reverse("core:goal_dna_management"))

        self.assertEqual(response.context["primary_goal"]["progress"]["lifetime_progress_value"], 2)
        self.assertEqual(response.context["additional_goals"][0]["progress"]["lifetime_progress_value"], 1)
        self.assertContains(
            response,
            reverse("core:goal_progress", args=[self.primary.pk]),
        )
        self.assertContains(
            response,
            reverse("core:goal_progress", args=[self.additional.pk]),
        )

    def test_management_renders_paused_and_completed_historical_progress(self):
        paused = self.create_goal(
            "Paused history",
            status=UserGoal.STATUS_PAUSED,
        )
        completed = self.create_goal(
            "Completed history",
            status=UserGoal.STATUS_COMPLETED,
        )
        self.create_entry(paused, progress="2.00")
        self.create_entry(completed, progress="3.00")

        response = self.client.get(reverse("core:goal_dna_management"))

        paused_data = next(goal for goal in response.context["paused_goals"] if goal["id"] == paused.pk)
        completed_data = next(goal for goal in response.context["completed_goals"] if goal["id"] == completed.pk)
        self.assertEqual(paused_data["progress"]["lifetime_progress_value"], 2)
        self.assertEqual(completed_data["progress"]["lifetime_progress_value"], 3)
        self.assertContains(response, "HISTORICAL PROGRESS", count=2)

    def test_detail_timeline_is_goal_only_newest_first_with_source_links(self):
        older = self.create_entry(
            self.primary,
            title="Older primary action",
            completed_at=timezone.now() - timedelta(days=2),
        )
        newer = self.create_entry(
            self.primary,
            title="Newer primary action",
            completed_at=timezone.now(),
        )
        other = self.create_entry(
            self.additional,
            title="Other goal action",
        )

        response = self.client.get(
            reverse("core:goal_progress", args=[self.primary.pk])
        )

        timeline = response.context["timeline"]
        self.assertEqual(
            [item["action_title"] for item in timeline],
            [newer.action_title, older.action_title],
        )
        self.assertNotContains(response, other.action_title)
        self.assertContains(
            response,
            reverse("core:view_summary", args=[newer.digital_summary_id]),
        )
        self.assertContains(
            response,
            reverse("core:view_summary", args=[older.digital_summary_id]),
        )


class AdaptiveGoalRescueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="adaptive-owner",
            password="test-password",
        )
        self.primary = self.create_goal("Adaptive primary", is_primary=True)
        self.additional = self.create_goal("Adaptive additional")
        self.client.force_login(self.user)

    def create_goal(self, title, *, is_primary=False):
        goal = UserGoal.objects.create(
            user=self.user,
            title=title,
            why_it_matters=f"Why {title} matters",
            current_focus="Adaptive focus",
            progress_unit="sessions",
            weekly_target=10,
            is_primary=is_primary,
            status=UserGoal.STATUS_ACTIVE,
        )
        for size, minutes, progress in (
            (GoalAction.SIZE_MINIMUM, 5, 1),
            (GoalAction.SIZE_STANDARD, 20, 2),
            (GoalAction.SIZE_DEEP, 45, 4),
        ):
            GoalAction.objects.create(
                goal=goal,
                size=size,
                title=f"{title} {size}",
                duration_minutes=minutes,
                progress_value=progress,
            )
        return goal

    def complete_size(self, goal, size, *, when=None):
        action = goal.actions.get(size=size)
        summary = DigitalSummary.objects.create(
            user=self.user,
            screen_time_minutes=500,
            wellness_score=70,
            category="Balanced",
            insight="Adaptive history",
            goal_rescue_snapshot={"status": "ready"},
        )
        entry = MomentumEntry.objects.create(
            user=self.user,
            goal=goal,
            action=action,
            digital_summary=summary,
            action_title=action.title,
            action_size=action.size,
            duration_minutes=action.duration_minutes,
            progress_value=action.progress_value,
            progress_unit=goal.progress_unit,
        )
        if when is not None:
            MomentumEntry.objects.filter(pk=entry.pk).update(completed_at=when)
            entry.refresh_from_db()
        return entry

    def test_no_history_uses_largest_action_that_fits(self):
        rescue = build_goal_rescue(self.user, 500)

        self.assertEqual(rescue["action_size"], GoalAction.SIZE_DEEP)
        self.assertIn("largest step", rescue["selection_reason"])

    def test_strong_deep_history_keeps_deep_when_it_fits(self):
        for _ in range(4):
            self.complete_size(self.primary, GoalAction.SIZE_DEEP)

        rescue = build_goal_rescue(self.user, 500)

        self.assertEqual(rescue["action_size"], GoalAction.SIZE_DEEP)
        self.assertIn("recent completions", rescue["selection_reason"])

    def test_strong_smaller_history_can_prefer_realistic_smaller_action(self):
        for _ in range(4):
            self.complete_size(self.primary, GoalAction.SIZE_MINIMUM)

        rescue = build_goal_rescue(self.user, 500)

        self.assertEqual(rescue["action_size"], GoalAction.SIZE_MINIMUM)
        self.assertIn("recent", rescue["selection_reason"])

    def test_action_that_does_not_fit_is_never_selected(self):
        for _ in range(4):
            self.complete_size(self.primary, GoalAction.SIZE_DEEP)

        rescue = build_goal_rescue(self.user, 250)

        self.assertNotEqual(rescue["action_size"], GoalAction.SIZE_DEEP)
        self.assertLessEqual(rescue["action_minutes"], 25)

    def test_other_goals_history_does_not_influence_primary(self):
        for _ in range(4):
            self.complete_size(self.additional, GoalAction.SIZE_MINIMUM)

        rescue = build_goal_rescue(self.user, 500)

        self.assertEqual(rescue["action_size"], GoalAction.SIZE_DEEP)
        self.assertIn("largest step", rescue["selection_reason"])

    def test_switch_uses_only_new_primary_history(self):
        for _ in range(4):
            self.complete_size(self.primary, GoalAction.SIZE_MINIMUM)
            self.complete_size(self.additional, GoalAction.SIZE_DEEP)

        before_switch = build_goal_rescue(self.user, 500)
        self.client.post(
            reverse("core:make_primary_goal", args=[self.additional.pk])
        )
        after_switch = build_goal_rescue(self.user, 500)

        self.assertEqual(before_switch["goal_id"], self.primary.pk)
        self.assertEqual(before_switch["action_size"], GoalAction.SIZE_MINIMUM)
        self.assertEqual(after_switch["goal_id"], self.additional.pk)
        self.assertEqual(after_switch["action_size"], GoalAction.SIZE_DEEP)

    def test_weekly_remaining_target_is_explainable(self):
        old_time = timezone.now() - timedelta(days=10)
        for _ in range(2):
            self.complete_size(
                self.primary,
                GoalAction.SIZE_MINIMUM,
                when=old_time,
            )
            self.complete_size(
                self.primary,
                GoalAction.SIZE_STANDARD,
                when=old_time,
            )
        self.primary.weekly_target = 2
        self.primary.save(update_fields=["weekly_target"])

        rescue = build_goal_rescue(self.user, 250)

        self.assertEqual(rescue["action_size"], GoalAction.SIZE_STANDARD)
        self.assertIn("remaining target", rescue["selection_reason"])

    def test_history_older_than_thirty_days_has_no_influence(self):
        old_time = timezone.now() - timedelta(days=31)
        for _ in range(5):
            self.complete_size(
                self.primary,
                GoalAction.SIZE_MINIMUM,
                when=old_time,
            )

        rescue = build_goal_rescue(self.user, 500)

        self.assertEqual(rescue["action_size"], GoalAction.SIZE_DEEP)
        self.assertIn("largest step", rescue["selection_reason"])

    def test_smallest_action_fallback_remains_when_little_time_is_available(self):
        for _ in range(4):
            self.complete_size(self.primary, GoalAction.SIZE_DEEP)

        rescue = build_goal_rescue(self.user, 1)

        self.assertEqual(rescue["action_size"], GoalAction.SIZE_MINIMUM)
        self.assertEqual(rescue["action_minutes"], 5)

    def test_frozen_summary_does_not_change_after_behavior_changes(self):
        original = freeze_goal_rescue_snapshot(
            build_goal_rescue(self.user, 500)
        )
        summary = DigitalSummary.objects.create(
            user=self.user,
            screen_time_minutes=500,
            wellness_score=70,
            category="Balanced",
            insight="Frozen adaptive rescue",
            goal_rescue_snapshot=original,
        )
        for _ in range(4):
            self.complete_size(self.primary, GoalAction.SIZE_MINIMUM)

        summary.refresh_from_db()
        self.assertEqual(goal_rescue_for_summary(summary), original)
        self.assertEqual(original["action_size"], GoalAction.SIZE_DEEP)

    def test_new_summary_snapshots_adaptive_reason_and_completion_is_idempotent(self):
        for _ in range(4):
            self.complete_size(self.primary, GoalAction.SIZE_MINIMUM)

        response = self.client.post(
            reverse("core:home"),
            {"screen_time": 500, "mood": "Calm", "goal": "Study"},
        )
        self.assertEqual(response.status_code, 302)
        summary = DigitalSummary.objects.filter(
            user=self.user,
        ).order_by("-id").first()
        snapshot = summary.goal_rescue_snapshot

        self.assertEqual(snapshot["action_size"], GoalAction.SIZE_MINIMUM)
        self.assertIn("recent", snapshot["selection_reason"])

        self.client.post(
            reverse("core:complete_goal_rescue"),
            {"summary_id": summary.pk},
        )
        self.client.post(
            reverse("core:complete_goal_rescue"),
            {"summary_id": summary.pk},
        )

        self.assertEqual(
            MomentumEntry.objects.filter(digital_summary=summary).count(),
            1,
        )
        entry = MomentumEntry.objects.get(digital_summary=summary)
        self.assertEqual(entry.goal_id, self.primary.pk)
        self.assertEqual(entry.action_title, snapshot["action_title"])
        self.assertEqual(entry.action_size, snapshot["action_size"])


class GoalRescueOutcomeAdaptiveV2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="outcome-owner", password="pw")
        self.other = User.objects.create_user(username="outcome-other", password="pw")
        self.primary = self.create_goal("Outcome primary", True)
        self.additional = self.create_goal("Outcome additional", False)
        self.client.force_login(self.user)

    def create_goal(self, title, primary):
        goal = UserGoal.objects.create(
            user=self.user, title=title, why_it_matters="Reliable outcomes",
            progress_unit="sessions", weekly_target=8, is_primary=primary,
        )
        for size, minutes, value in (("minimum", 5, 1), ("standard", 20, 2), ("deep", 45, 4)):
            GoalAction.objects.create(goal=goal, size=size, title=f"{title} {size}", duration_minutes=minutes, progress_value=value)
        return goal

    def ready_summary(self, goal=None, size=None):
        goal = goal or self.primary
        action = goal.actions.get(size=size) if size else goal.actions.get(size="deep")
        snapshot = {
            "status": "ready", "goal_id": goal.id, "goal_title": goal.title,
            "action_id": action.id, "action_title": action.title,
            "action_size": action.size, "action_minutes": action.duration_minutes,
            "action_progress_value": str(action.progress_value),
            "progress_unit": goal.progress_unit, "selection_reason": "Frozen reason",
        }
        return DigitalSummary.objects.create(
            user=self.user, screen_time_minutes=500, wellness_score=70,
            category="Good", insight="Outcome summary", goal_rescue_snapshot=snapshot,
        )

    def interaction(self, size, status, *, goal=None, shown_at=None):
        goal = goal or self.primary
        summary = self.ready_summary(goal, size)
        outcome = ensure_goal_rescue_outcome(summary)
        outcome.status = status
        outcome.shown_at = shown_at or timezone.now()
        if status == "completed":
            outcome.completed_at = outcome.shown_at
            action = goal.actions.get(size=size)
            MomentumEntry.objects.create(
                user=self.user, goal=goal, action=action, digital_summary=summary,
                action_title=action.title, action_size=size,
                duration_minutes=action.duration_minutes,
                progress_value=action.progress_value, progress_unit=goal.progress_unit,
            )
        elif status == "skipped":
            outcome.skipped_at = outcome.shown_at
        outcome.save()
        return outcome

    def test_new_ready_summary_has_one_shown_outcome(self):
        self.client.post(reverse("core:home"), {"screen_time": 500, "mood": "Calm", "goal": "Study"})
        summary = DigitalSummary.objects.latest("id")
        outcome = summary.goal_rescue_outcome
        self.assertEqual(outcome.status, "shown")
        self.assertEqual(outcome.goal_id, self.primary.id)
        self.assertEqual(GoalRescueOutcome.objects.filter(digital_summary=summary).count(), 1)
        ensure_goal_rescue_outcome(summary)
        self.assertEqual(GoalRescueOutcome.objects.filter(digital_summary=summary).count(), 1)

    def test_completion_updates_outcome_and_remains_idempotent(self):
        summary = self.ready_summary()
        self.client.post(reverse("core:complete_goal_rescue"), {"summary_id": summary.id})
        self.client.post(reverse("core:complete_goal_rescue"), {"summary_id": summary.id})
        outcome = GoalRescueOutcome.objects.get(digital_summary=summary)
        self.assertEqual(outcome.status, "completed")
        self.assertIsNotNone(outcome.completed_at)
        self.assertEqual(MomentumEntry.objects.filter(digital_summary=summary).count(), 1)

    def test_skip_is_idempotent_creates_no_momentum_and_cannot_override_completed(self):
        summary = self.ready_summary()
        url = reverse("core:skip_goal_rescue")
        self.client.post(url, {"summary_id": summary.id})
        self.client.post(url, {"summary_id": summary.id})
        outcome = GoalRescueOutcome.objects.get(digital_summary=summary)
        self.assertEqual(outcome.status, "skipped")
        self.assertFalse(MomentumEntry.objects.filter(digital_summary=summary).exists())

        completed = self.ready_summary(size="standard")
        self.client.post(reverse("core:complete_goal_rescue"), {"summary_id": completed.id})
        self.client.post(url, {"summary_id": completed.id})
        self.assertEqual(completed.goal_rescue_outcome.status, "completed")

    def test_skip_security_methods_and_legacy_guard(self):
        self.assertEqual(self.client.get(reverse("core:skip_goal_rescue")).status_code, 405)
        foreign = DigitalSummary.objects.create(user=self.other, screen_time_minutes=1, wellness_score=1, category="x", insight="x")
        self.assertEqual(self.client.post(reverse("core:skip_goal_rescue"), {"summary_id": foreign.id}).status_code, 404)
        legacy = DigitalSummary.objects.create(user=self.user, screen_time_minutes=1, wellness_score=1, category="x", insight="x")
        self.client.post(reverse("core:skip_goal_rescue"), {"summary_id": legacy.id})
        self.assertFalse(GoalRescueOutcome.objects.filter(digital_summary=legacy).exists())
        self.client.logout()
        response = self.client.post(reverse("core:skip_goal_rescue"), {"summary_id": legacy.id})
        self.assertEqual(response.status_code, 302)

    def test_foreign_summary_cannot_be_completed(self):
        foreign = DigitalSummary.objects.create(user=self.other, screen_time_minutes=1, wellness_score=1, category="x", insight="x")
        self.assertEqual(self.client.post(reverse("core:complete_goal_rescue"), {"summary_id": foreign.id}).status_code, 404)

    def test_insufficient_outcomes_preserve_phase1_fallback(self):
        self.interaction("deep", "skipped")
        self.interaction("deep", "skipped")
        self.assertEqual(build_goal_rescue(self.user, 500)["action_size"], "deep")

    def test_completed_deep_wins_and_skipped_deep_allows_standard(self):
        for _ in range(4): self.interaction("deep", "completed")
        self.assertEqual(build_goal_rescue(self.user, 500)["action_size"], "deep")

        GoalRescueOutcome.objects.all().delete(); MomentumEntry.objects.all().delete(); DigitalSummary.objects.all().delete()
        for _ in range(3): self.interaction("deep", "skipped")
        for _ in range(3): self.interaction("standard", "completed")
        rescue = build_goal_rescue(self.user, 500)
        self.assertEqual(rescue["action_size"], "standard")
        self.assertIn("Recent activity", rescue["selection_reason"])

    def test_minimum_evidence_ineligible_guard_and_other_goal_isolation(self):
        for _ in range(4): self.interaction("minimum", "completed")
        self.assertEqual(build_goal_rescue(self.user, 500)["action_size"], "minimum")
        for _ in range(4): self.interaction("deep", "completed", goal=self.additional)
        self.assertNotEqual(build_goal_rescue(self.user, 250)["action_size"], "deep")

    def test_switch_old_data_and_thirty_day_expiry_are_isolated(self):
        old = timezone.now() - timedelta(days=31)
        for _ in range(4): self.interaction("minimum", "skipped", shown_at=old)
        self.assertEqual(build_goal_rescue(self.user, 500)["action_size"], "deep")
        for _ in range(4): self.interaction("standard", "completed", goal=self.additional)
        self.client.post(reverse("core:make_primary_goal", args=[self.additional.id]))
        self.assertEqual(build_goal_rescue(self.user, 500)["action_size"], "standard")

    def test_legacy_momentum_is_positive_only_and_repetition_is_deterministic(self):
        for _ in range(4):
            action = self.primary.actions.get(size="minimum")
            summary = self.ready_summary(size="minimum")
            MomentumEntry.objects.create(user=self.user, goal=self.primary, action=action, digital_summary=summary, action_title=action.title, action_size="minimum", duration_minutes=5, progress_value=1, progress_unit="sessions")
        first = build_goal_rescue(self.user, 500)
        second = build_goal_rescue(self.user, 500)
        self.assertEqual(first["action_id"], second["action_id"])
        self.assertEqual(first["action_size"], "minimum")

    def test_v2_snapshot_reason_freezes_and_weekly_signal_survives(self):
        for _ in range(3): self.interaction("deep", "skipped")
        for _ in range(3): self.interaction("standard", "completed")
        snapshot = freeze_goal_rescue_snapshot(build_goal_rescue(self.user, 500))
        summary = DigitalSummary.objects.create(user=self.user, screen_time_minutes=500, wellness_score=70, category="Good", insight="frozen", goal_rescue_snapshot=snapshot)
        self.interaction("minimum", "completed")
        self.assertEqual(goal_rescue_for_summary(summary), snapshot)
        self.assertIn("Recent activity", snapshot["selection_reason"])


class WeeklyReviewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="weekly-owner", password="pw")
        self.other = User.objects.create_user(username="weekly-other", password="pw")
        self.primary = self.goal("Weekly primary", True, "sessions", 5)
        self.additional = self.goal("Weekly additional", False, "minutes", 60)
        self.client.force_login(self.user)

    def goal(self, title, primary, unit, target, status="active"):
        goal = UserGoal.objects.create(user=self.user, title=title, why_it_matters="weekly", progress_unit=unit, weekly_target=target, is_primary=primary, status=status)
        for size, minutes, value in (("minimum",5,1),("standard",20,2),("deep",45,4)):
            GoalAction.objects.create(goal=goal,size=size,title=f"{title} {size}",duration_minutes=minutes,progress_value=value)
        return goal

    def activity(self, goal, size="standard", status="completed", when=None):
        action=goal.actions.get(size=size); when=when or timezone.now()
        summary=DigitalSummary.objects.create(user=self.user,screen_time_minutes=200,wellness_score=70,category="Good",insight="weekly",goal_rescue_snapshot={"status":"ready","goal_id":goal.id,"action_id":action.id,"action_size":size,"action_title":action.title,"action_minutes":action.duration_minutes,"action_progress_value":str(action.progress_value),"progress_unit":goal.progress_unit})
        outcome=GoalRescueOutcome.objects.create(user=self.user,digital_summary=summary,goal=goal,action=action,action_size=size,action_title=action.title,status=status,shown_at=when,completed_at=when if status=="completed" else None,skipped_at=when if status=="skipped" else None)
        if status=="completed":
            entry=MomentumEntry.objects.create(user=self.user,goal=goal,action=action,digital_summary=summary,action_title=action.title,action_size=size,duration_minutes=action.duration_minutes,progress_value=action.progress_value,progress_unit=goal.progress_unit)
            MomentumEntry.objects.filter(id=entry.id).update(completed_at=when)
        return outcome

    def test_empty_week_and_anonymous_redirect(self):
        review=build_weekly_review(self.user)
        self.assertFalse(review["has_activity"]); self.assertEqual(review["completed_actions"],0)
        response=self.client.get(reverse("core:weekly_review")); self.assertContains(response,"No completed Momentum activity")
        self.client.logout(); self.assertEqual(self.client.get(reverse("core:weekly_review")).status_code,302)

    def test_weekly_totals_outcomes_percentage_and_units(self):
        self.activity(self.primary,"minimum","completed")
        self.activity(self.primary,"standard","skipped")
        self.activity(self.additional,"standard","completed")
        review=build_weekly_review(self.user)
        self.assertEqual(review["completed_actions"],2)
        self.assertEqual(review["reclaimed_minutes"],25)
        self.assertEqual(review["recommendations_shown"],3)
        self.assertEqual(review["recommendations_completed"],2)
        self.assertEqual(review["recommendations_skipped"],1)
        self.assertEqual(review["completion_percent"],67)
        self.assertEqual({item["unit"] for item in review["progress_totals"]},{"sessions","minutes"})

    def test_goal_values_separate_most_active_size_and_insights(self):
        self.activity(self.primary,"standard","completed")
        self.activity(self.primary,"standard","completed")
        self.activity(self.additional,"minimum","completed")
        review=build_weekly_review(self.user)
        rows={row["id"]:row for row in review["goal_rows"]}
        self.assertEqual(rows[self.primary.id]["progress"]["current_week_progress"],4)
        self.assertEqual(rows[self.additional.id]["progress"]["current_week_progress"],1)
        self.assertEqual(review["most_active_goal"]["id"],self.primary.id)
        self.assertEqual(review["most_completed_size"]["size"],"standard")
        self.assertTrue(any("reclaimed" in insight for insight in review["insights"]))

    def test_recent_activity_newest_first_and_other_user_excluded(self):
        older=self.activity(self.primary,when=timezone.now()-timedelta(days=1))
        newer=self.activity(self.primary,when=timezone.now())
        other_goal=UserGoal.objects.create(user=self.other,title="private",why_it_matters="private",progress_unit="sessions",weekly_target=1,is_primary=True)
        other_summary=DigitalSummary.objects.create(user=self.other,screen_time_minutes=1,wellness_score=1,category="x",insight="x")
        GoalRescueOutcome.objects.create(user=self.other,digital_summary=other_summary,goal=other_goal,action_size="minimum",action_title="private",shown_at=timezone.now())
        review=build_weekly_review(self.user)
        self.assertEqual([entry.digital_summary_id for entry in review["recent_activity"]],[newer.digital_summary_id,older.digital_summary_id])
        self.assertEqual(review["recommendations_shown"],2)

    def test_paused_completed_history_and_next_step_are_reported(self):
        paused=self.goal("Paused weekly",False,"sessions",3,"paused")
        completed=self.goal("Completed weekly",False,"sessions",3,"completed")
        self.activity(paused); self.activity(completed)
        review=build_weekly_review(self.user)
        statuses={row["status"] for row in review["goal_rows"]}
        self.assertIn("paused",statuses); self.assertIn("completed",statuses)
        self.assertIsNotNone(review["next_step"])
        self.assertEqual(review["next_step"]["goal_title"],self.primary.title)

    def test_weekly_page_renders_source_and_discoverability(self):
        outcome=self.activity(self.primary)
        response=self.client.get(reverse("core:weekly_review"))
        self.assertContains(response,reverse("core:view_summary",args=[outcome.digital_summary_id]))
        ledger=self.client.get(reverse("core:momentum_ledger"))
        self.assertContains(ledger,reverse("core:weekly_review"))


class MVPFinalizationTests(TestCase):
    """Focused coverage for health, milestones, filters and review history."""

    def setUp(self):
        self.user = User.objects.create_user(username="final-user", password="pw")
        self.other = User.objects.create_user(username="final-other", password="pw")
        self.client.force_login(self.user)
        self.goal = UserGoal.objects.create(
            user=self.user, title="Finish MVP", why_it_matters="Ship carefully",
            progress_unit="sessions", weekly_target=10, is_primary=True,
        )
        for size, minutes, value in (("minimum", 10, 1), ("standard", 25, 3), ("deep", 60, 6)):
            GoalAction.objects.create(goal=self.goal, size=size, title=f"{size} work", duration_minutes=minutes, progress_value=value)

    def entry(self, *, goal=None, size="minimum", minutes=10, progress=1, when=None, unit=None):
        goal = goal or self.goal
        summary = DigitalSummary.objects.create(
            user=goal.user, screen_time_minutes=120, wellness_score=80,
            category="Good", insight="Stored",
        )
        entry = MomentumEntry.objects.create(
            user=goal.user, goal=goal, digital_summary=summary,
            action_title=f"{size} action", action_size=size,
            duration_minutes=minutes, progress_value=progress,
            progress_unit=unit or goal.progress_unit,
        )
        if when:
            MomentumEntry.objects.filter(pk=entry.pk).update(completed_at=when)
            entry.refresh_from_db()
        return entry

    def outcome(self, *, goal=None, size="minimum", status="shown", when=None):
        goal = goal or self.goal
        summary = DigitalSummary.objects.create(
            user=goal.user, screen_time_minutes=100, wellness_score=75,
            category="Good", insight="Stored",
        )
        return GoalRescueOutcome.objects.create(
            user=goal.user, digital_summary=summary, goal=goal,
            action_size=size, action_title=f"{size} action", status=status,
            shown_at=when or timezone.now(),
        )

    def health(self, entries=(), outcomes=(), goal=None):
        goal = goal or self.goal
        progress = build_goal_progress(goal, entries)
        return build_goal_health(goal, progress, entries, outcomes)["label"]

    def test_goal_health_all_states_and_determinism(self):
        self.assertEqual(self.health(), "No activity yet")
        now = timezone.now()
        one = self.entry(progress=1, when=now)
        self.assertEqual(self.health([one]), "Building momentum")
        eight = self.entry(progress=8, when=now)
        self.assertEqual(self.health([eight]), "On track")
        ten = self.entry(progress=10, when=now)
        self.assertEqual(self.health([ten]), "Ahead")
        old = self.entry(progress=1, when=now - timedelta(days=8))
        self.assertEqual(self.health([old]), "Needs attention")
        self.goal.status = UserGoal.STATUS_PAUSED
        self.assertEqual(self.health([one]), "Paused")
        self.goal.status = UserGoal.STATUS_COMPLETED
        self.assertEqual(self.health([one]), "Completed")
        self.goal.status = UserGoal.STATUS_ACTIVE
        self.assertEqual(self.health([one]), self.health([one]))

    def test_goal_health_repeated_skips_and_cross_goal_isolation(self):
        entries = [self.entry(progress=1)]
        skips = [self.outcome(status="skipped") for _ in range(3)]
        self.assertEqual(self.health(entries, skips), "Needs attention")
        other_goal = UserGoal.objects.create(user=self.other, title="Other", why_it_matters="x", progress_unit="items", weekly_target=1, is_primary=True)
        other_entry = self.entry(goal=other_goal, progress=1)
        self.assertEqual(self.health(entries), "Building momentum")
        self.assertNotEqual(other_entry.goal_id, self.goal.id)

    def test_curated_milestones_exact_thresholds_and_order(self):
        entries = [self.entry(minutes=60 if i == 0 else 30, size="deep" if i == 0 else "minimum") for i in range(10)]
        milestones = build_goal_milestones(self.goal, entries)
        keys = [item["key"] for item in milestones]
        for key in ("first_action", "five_actions", "ten_actions", "minutes_60", "minutes_300", "first_deep"):
            self.assertIn(key, keys)
        self.assertEqual(milestones, sorted(milestones, key=lambda item: (item["achieved_at"], item["key"])))

    def test_weekly_target_milestones_and_no_fabrication(self):
        self.assertEqual(build_goal_milestones(self.goal, []), [])
        reached = self.entry(progress=10)
        exceeded = self.entry(progress=1)
        keys = {item["key"] for item in build_goal_milestones(self.goal, [reached, exceeded])}
        self.assertIn("weekly_reached", keys)
        self.assertIn("weekly_exceeded", keys)

    def test_goal_progress_v2_outcomes_sizes_health_milestone_and_ownership(self):
        self.entry(size="deep", minutes=60, progress=6)
        self.outcome(size="deep", status="completed")
        self.outcome(size="standard", status="skipped")
        response = self.client.get(reverse("core:goal_progress", args=[self.goal.id]))
        self.assertContains(response, "Recommendations shown")
        self.assertContains(response, "Deep completed")
        self.assertContains(response, "Standard skipped")
        self.assertContains(response, "Momentum status")
        self.assertContains(response, "60 reclaimed minutes")
        private_goal = UserGoal.objects.create(user=self.other, title="Private", why_it_matters="x", progress_unit="x", weekly_target=1, is_primary=True)
        self.assertEqual(self.client.get(reverse("core:goal_progress", args=[private_goal.id])).status_code, 404)

    def test_goal_progress_paused_and_completed_preserve_analytics(self):
        self.entry()
        for status, label in ((UserGoal.STATUS_PAUSED, "Paused"), (UserGoal.STATUS_COMPLETED, "Completed")):
            self.goal.status = status
            self.goal.save(update_fields=["status"])
            response = self.client.get(reverse("core:goal_progress", args=[self.goal.id]))
            self.assertContains(response, label)
            self.assertContains(response, "1")

    def test_outcome_analytics_completion_percentage_and_breakdowns(self):
        outcomes = [self.outcome(size="deep", status="completed"), self.outcome(size="deep", status="skipped"), self.outcome(size="minimum")]
        result = build_goal_outcome_analytics(outcomes)
        self.assertEqual((result["shown"], result["completed"], result["skipped"], result["pending"]), (3, 1, 1, 1))
        self.assertEqual(result["completion_percent"], 33)

    def test_momentum_filters_goal_size_period_invalid_and_totals(self):
        self.entry(size="minimum", minutes=10)
        self.entry(size="deep", minutes=60)
        response = self.client.get(reverse("core:momentum_ledger"), {"goal": self.goal.id, "size": "deep", "period": "week"})
        self.assertEqual(response.context["filtered_entry_count"], 1)
        self.assertEqual(response.context["filtered_summary"]["reclaimed_time"], "1h")
        response = self.client.get(reverse("core:momentum_ledger"), {"goal": "bad", "size": "unknown", "period": "nonsense"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filtered_entry_count"], 2)
        self.assertEqual(response.context["selected_period"], "all")

    def test_momentum_30_day_all_time_empty_and_cross_user_exclusion(self):
        old = self.entry(when=timezone.now() - timedelta(days=45))
        self.entry()
        private_goal = UserGoal.objects.create(user=self.other, title="Private", why_it_matters="x", progress_unit="x", weekly_target=1, is_primary=True)
        self.entry(goal=private_goal)
        recent = self.client.get(reverse("core:momentum_ledger"), {"period": "30days"})
        self.assertEqual(recent.context["filtered_entry_count"], 1)
        all_time = self.client.get(reverse("core:momentum_ledger"), {"period": "all"})
        self.assertEqual(all_time.context["filtered_entry_count"], 2)
        empty = self.client.get(reverse("core:momentum_ledger"), {"size": "deep"})
        self.assertEqual(empty.context["filtered_entry_count"], 0)
        self.assertNotContains(all_time, private_goal.title)
        self.assertIsNotNone(old)

    def test_weekly_history_boundaries_metrics_and_no_next_step(self):
        selected = timezone.localdate() - timedelta(days=8)
        week_start = selected - timedelta(days=selected.weekday())
        when = timezone.make_aware(datetime.combine(week_start + timedelta(days=2), datetime.min.time()))
        self.entry(progress=3, when=when)
        self.outcome(status="skipped", when=when)
        response = self.client.get(reverse("core:weekly_review"), {"week": selected.isoformat()})
        review = response.context["review"]
        self.assertEqual(review["week_start"], week_start)
        self.assertEqual(review["week_end"], week_start + timedelta(days=6))
        self.assertEqual(review["completed_actions"], 1)
        self.assertEqual(review["recommendations_skipped"], 1)
        self.assertIsNone(review["next_step"])
        self.assertContains(response, "Historical review")

    def test_weekly_history_future_and_malformed_dates_are_safe(self):
        current = self.client.get(reverse("core:weekly_review"))
        future = self.client.get(reverse("core:weekly_review"), {"week": (timezone.localdate() + timedelta(days=14)).isoformat()})
        malformed = self.client.get(reverse("core:weekly_review"), {"week": "not-a-date"})
        self.assertEqual(future.context["review"]["week_start"], current.context["review"]["week_start"])
        self.assertEqual(malformed.context["review"]["week_start"], current.context["review"]["week_start"])

    def test_historical_review_goal_attribution_mixed_units_and_lifecycle(self):
        selected = timezone.localdate() - timedelta(days=8)
        week_start = selected - timedelta(days=selected.weekday())
        when = timezone.make_aware(datetime.combine(week_start, datetime.min.time()))
        paused = UserGoal.objects.create(user=self.user, title="Paused history", why_it_matters="x", progress_unit="pages", weekly_target=5, status="paused")
        self.entry(goal=paused, progress=2, when=when)
        self.entry(progress=3, unit="sessions", when=when)
        review = build_weekly_review(self.user, today=selected)
        self.assertEqual({item["unit"] for item in review["progress_totals"]}, {"pages", "sessions"})
        self.assertIn("Paused history", {row["title"] for row in review["goal_rows"]})

    def test_protected_analytics_and_state_change_security(self):
        self.client.logout()
        for url in (reverse("core:weekly_review"), reverse("core:goal_progress", args=[self.goal.id]), reverse("core:momentum_ledger")):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("core:skip_goal_rescue")).status_code, 405)
        self.assertEqual(self.client.get(reverse("core:complete_goal_rescue")).status_code, 405)
        self.assertEqual(self.client.post(reverse("core:skip_goal_rescue"), {"summary_id": "bad"}).status_code, 404)


class SeedDemoDataCommandTests(TestCase):
    @override_settings(DEBUG=True)
    def test_command_is_repeatable_and_scoped_to_demo_user(self):
        ordinary = User.objects.create_user(username="ordinary-user", password="pw")
        call_command("seed_demo_data", stdout=StringIO())
        demo = User.objects.get(username="beyondscreen_demo")
        first_counts = (
            demo.goals.count(),
            demo.digital_summaries.count(),
            demo.momentum_entries.count(),
            demo.goal_rescue_outcomes.count(),
        )

        call_command("seed_demo_data", stdout=StringIO())
        demo.refresh_from_db()

        self.assertEqual(first_counts, (2, 9, 6, 9))
        self.assertEqual(first_counts, (
            demo.goals.count(),
            demo.digital_summaries.count(),
            demo.momentum_entries.count(),
            demo.goal_rescue_outcomes.count(),
        ))
        self.assertTrue(User.objects.filter(pk=ordinary.pk).exists())

    @override_settings(DEBUG=False)
    def test_command_is_disabled_outside_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo_data", stdout=StringIO())
