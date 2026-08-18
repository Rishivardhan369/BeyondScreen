from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.conf import settings
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from .models import DigitalSummary, GoalAction, MomentumEntry, UserGoal
from .services import build_goal_rescue, generate_postcard


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
