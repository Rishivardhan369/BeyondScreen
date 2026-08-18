from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.conf import settings
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from .models import DigitalSummary, GoalAction, MomentumEntry, UserGoal
from .services import (
    build_goal_rescue,
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

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Here’s a note for")
        self.assertContains(response, "4h 05m")
        self.assertContains(response, "Download PNG")
        self.assertContains(response, "Copy entire postcard")

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

        self.assertEqual(response.status_code, 404)


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
