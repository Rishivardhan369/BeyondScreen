"""Create deterministic development-only data for faculty demonstrations."""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import (
    DigitalSummary,
    GoalAction,
    GoalRescueOutcome,
    MomentumEntry,
    UserGoal,
)


DEMO_USERNAME = "beyondscreen_demo"


class Command(BaseCommand):
    help = "Create repeatable BeyondScreen demo data (development only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Explicitly delete and rebuild only the dedicated demo account.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_data is disabled when DEBUG=False.")

        if options["reset"]:
            User.objects.filter(username=DEMO_USERNAME).delete()

        user, _ = User.objects.get_or_create(
            username=DEMO_USERNAME,
            defaults={
                "email": "demo@beyondscreen.local",
                "first_name": "Faculty",
                "last_name": "Demo",
            },
        )
        user.set_password("BeyondScreenDemo123!")
        user.save(update_fields=["password"])

        # Rebuild only this dedicated account so repeated runs remain deterministic.
        user.digital_summaries.all().delete()
        user.goals.all().delete()
        user.postcards.all().delete()

        primary = self._goal(
            user,
            title="Finish the final project presentation",
            reason="Present the work clearly and confidently to the faculty panel.",
            unit="slides",
            target=12,
            primary=True,
            actions=(
                ("minimum", "Polish one presentation slide", 8, 1),
                ("standard", "Rehearse one complete section", 25, 3),
                ("deep", "Run a full timed presentation", 55, 6),
            ),
        )
        secondary = self._goal(
            user,
            title="Build a sustainable fitness routine",
            reason="Have more energy and focus during demanding project weeks.",
            unit="workouts",
            target=3,
            primary=False,
            actions=(
                ("minimum", "Complete a mobility reset", 10, 1),
                ("standard", "Take a focused strength session", 30, 1),
                ("deep", "Complete a full gym workout", 60, 1),
            ),
        )

        now = timezone.now()
        plan = [
            (20, primary, "minimum", "completed"),
            (17, primary, "standard", "completed"),
            (15, secondary, "minimum", "skipped"),
            (12, primary, "deep", "completed"),
            (9, secondary, "standard", "completed"),
            (6, primary, "minimum", "skipped"),
            (4, primary, "standard", "completed"),
            (2, secondary, "minimum", "completed"),
            (0, primary, "deep", "shown"),
        ]
        for index, (days_ago, goal, size, status) in enumerate(plan):
            action = goal.actions.get(size=size)
            when = now - timedelta(days=days_ago, hours=index)
            summary = DigitalSummary.objects.create(
                user=user,
                screen_time_minutes=180 + index * 17,
                wellness_score=78 - index * 2,
                category="Balanced" if index % 2 == 0 else "Needs attention",
                insight="Demo insight: one intentional action can reclaim part of this time.",
                goal_rescue_snapshot={
                    "status": "ready",
                    "goal_id": goal.id,
                    "goal_title": goal.title,
                    "action_id": action.id,
                    "action_title": action.title,
                    "action_size": action.size,
                    "action_minutes": action.duration_minutes,
                    "action_progress_value": str(action.progress_value),
                    "progress_unit": goal.progress_unit,
                    "selection_reason": "Selected from the demo goal ladder for the available time.",
                },
            )
            DigitalSummary.objects.filter(pk=summary.pk).update(created_at=when)
            outcome = GoalRescueOutcome.objects.create(
                user=user,
                digital_summary=summary,
                goal=goal,
                action=action,
                action_size=action.size,
                action_title=action.title,
                status=status,
                shown_at=when,
                completed_at=when if status == "completed" else None,
                skipped_at=when if status == "skipped" else None,
            )
            if outcome.status == "completed":
                entry = MomentumEntry.objects.create(
                    user=user,
                    goal=goal,
                    action=action,
                    digital_summary=summary,
                    action_title=action.title,
                    action_size=action.size,
                    duration_minutes=action.duration_minutes,
                    progress_value=action.progress_value,
                    progress_unit=goal.progress_unit,
                )
                MomentumEntry.objects.filter(pk=entry.pk).update(completed_at=when)

        self.stdout.write(self.style.SUCCESS(
            "Demo data ready for beyondscreen_demo (password: BeyondScreenDemo123!)."
        ))

    @staticmethod
    def _goal(user, *, title, reason, unit, target, primary, actions):
        goal = UserGoal.objects.create(
            user=user,
            title=title,
            why_it_matters=reason,
            current_focus="Prepare consistently without last-minute overload.",
            progress_unit=unit,
            weekly_target=target,
            is_primary=primary,
        )
        for size, title, minutes, value in actions:
            GoalAction.objects.create(
                goal=goal,
                size=size,
                title=title,
                duration_minutes=minutes,
                progress_value=value,
            )
        return goal
