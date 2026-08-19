"""Deterministic, query-bounded personal analytics."""

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean

from django.utils import timezone

from .models import DigitalSummary, GoalAction, GoalRescueOutcome, MomentumEntry, UserGoal


def _average(items):
    return round(mean(items), 1) if items else None


def build_personal_insights(user, *, today=None):
    today = today or timezone.localdate()
    start_30 = today - timedelta(days=29)
    start_14 = today - timedelta(days=13)
    start_7 = today - timedelta(days=6)
    start_90 = today - timedelta(days=89)

    summaries = list(
        DigitalSummary.objects.filter(user=user, created_at__date__gte=start_30)
        .only("created_at", "screen_time_minutes", "app_usage")
        .order_by("created_at", "id")
    )
    current_reports = [s for s in summaries if s.created_at.date() >= start_7]
    previous_reports = [s for s in summaries if start_14 <= s.created_at.date() < start_7]
    current_values = [s.screen_time_minutes for s in current_reports]
    previous_values = [s.screen_time_minutes for s in previous_reports]
    avg_7 = _average(current_values)
    previous_avg_7 = _average(previous_values)
    avg_30 = _average([s.screen_time_minutes for s in summaries])
    direction = "insufficient"
    change_percent = None
    if len(current_values) >= 2 and len(previous_values) >= 2 and previous_avg_7:
        change_percent = round((avg_7 - previous_avg_7) / previous_avg_7 * 100, 1)
        direction = "higher" if change_percent > 0 else "lower" if change_percent < 0 else "steady"

    entries = list(
        MomentumEntry.objects.filter(user=user, completed_at__date__gte=start_90)
        .select_related("goal")
        .order_by("completed_at", "id")
    )
    outcomes = list(
        GoalRescueOutcome.objects.filter(user=user, shown_at__date__gte=start_30)
        .only("status", "action_size", "shown_at")
        .order_by("shown_at", "id")
    )
    current_entries = [e for e in entries if timezone.localtime(e.completed_at).date() >= start_7]
    previous_entries = [e for e in entries if start_14 <= timezone.localtime(e.completed_at).date() < start_7]

    active_days = sorted({timezone.localtime(e.completed_at).date() for e in entries})
    recent_active_days = [day for day in active_days if day >= start_30]
    streaks = []
    run = 0
    previous = None
    for day in active_days:
        run = run + 1 if previous and day == previous + timedelta(days=1) else 1
        streaks.append(run)
        previous = day
    current_streak = 0
    if active_days and active_days[-1] in {today, today - timedelta(days=1)}:
        current_streak = streaks[-1]

    week_starts = {day - timedelta(days=day.weekday()) for day in active_days}
    current_week = today - timedelta(days=today.weekday())
    consecutive_weeks = 0
    cursor = current_week
    while cursor in week_starts:
        consecutive_weeks += 1
        cursor -= timedelta(days=7)

    reached_weeks = set()
    goal_targets = dict(UserGoal.objects.filter(user=user).values_list("id", "weekly_target"))
    progress = defaultdict(float)
    for entry in entries:
        if entry.goal_id and entry.goal_id in goal_targets:
            day = timezone.localtime(entry.completed_at).date()
            week = day - timedelta(days=day.weekday())
            progress[(entry.goal_id, week)] += float(entry.progress_value)
    for (goal_id, week), value in progress.items():
        if value >= float(goal_targets[goal_id]):
            reached_weeks.add(week)

    status_counts = Counter(o.status for o in outcomes)
    size_counts = Counter(e.action_size for e in entries if timezone.localtime(e.completed_at).date() >= start_30)
    size_labels = dict(GoalAction.SIZE_CHOICES)

    app_reports = [s for s in summaries if isinstance(s.app_usage, list) and s.app_usage]
    app_appearances = Counter()
    app_totals = Counter()
    top_apps = Counter()
    concentrations = []
    for summary in app_reports:
        valid = [a for a in summary.app_usage if isinstance(a, dict) and str(a.get("name", "")).strip() and isinstance(a.get("minutes"), int) and a["minutes"] >= 0]
        if not valid:
            continue
        for app in valid:
            name = str(app["name"]).strip()
            app_appearances[name] += 1
            app_totals[name] += app["minutes"]
        top = max(valid, key=lambda a: (a["minutes"], str(a["name"])))
        top_apps[str(top["name"]).strip()] += 1
        total = sum(a["minutes"] for a in valid)
        if total:
            concentrations.append(round(top["minutes"] / total * 100, 1))
    applicable = sum(top_apps.values())
    most_top = max(top_apps, key=lambda n: (top_apps[n], n), default=None)
    cumulative = max(app_totals, key=lambda n: (app_totals[n], n), default=None)
    app_change = None
    if cumulative and len(app_reports) >= 4:
        midpoint = len(app_reports) // 2
        halves = (app_reports[:midpoint], app_reports[midpoint:])
        averages = []
        for group in halves:
            values = []
            for summary in group:
                values.append(sum(a.get("minutes", 0) for a in summary.app_usage if isinstance(a, dict) and str(a.get("name", "")).strip() == cumulative and isinstance(a.get("minutes"), int)))
            averages.append(_average(values))
        if averages[0] is not None and averages[1] is not None:
            delta = round(averages[1] - averages[0], 1)
            app_change = {"direction": "higher" if delta > 0 else "lower" if delta < 0 else "steady", "minutes": abs(delta)}

    highest = max(summaries, key=lambda s: (s.screen_time_minutes, -s.id), default=None)
    lowest = min(summaries, key=lambda s: (s.screen_time_minutes, s.id), default=None)
    show_skipped = user.userprofile.show_skipped_rescue_statistics
    return {
        "screen_time": {
            "average_7": avg_7, "previous_average_7": previous_avg_7,
            "average_30": avg_30, "direction": direction,
            "change_percent": change_percent, "reports_7": len(current_values),
            "reports_30": len(summaries), "highest": highest, "lowest": lowest,
        },
        "momentum": {
            "reclaimed_7": sum(e.duration_minutes for e in current_entries),
            "previous_reclaimed_7": sum(e.duration_minutes for e in previous_entries),
            "completed_7": len(current_entries), "previous_completed_7": len(previous_entries),
            "current_streak": current_streak, "longest_streak": max(streaks, default=0),
            "active_days_30": len(recent_active_days), "weeks_with_momentum": len(week_starts),
            "consecutive_weeks": consecutive_weeks, "target_weeks_reached": len(reached_weeks),
        },
        "rescue": {
            "shown": len(outcomes), "completed": status_counts["completed"],
            "skipped": status_counts["skipped"] if show_skipped else None,
            "pending": status_counts["shown"],
            "completion_percent": round(status_counts["completed"] / len(outcomes) * 100) if outcomes else None,
            "show_skipped": show_skipped,
        },
        "action_sizes": [
            {"key": key, "label": size_labels.get(key, "Goal Step"), "count": size_counts[key]}
            for key in ("minimum", "standard", "deep")
        ],
        "apps": {
            "applicable_reports": applicable, "most_frequent_top": most_top,
            "most_frequent_top_count": top_apps[most_top] if most_top else 0,
            "cumulative_leader": cumulative, "cumulative_minutes": app_totals[cumulative] if cumulative else 0,
            "appearance_count": app_appearances[cumulative] if cumulative else 0,
            "average_top_concentration": _average(concentrations),
            "recent_change": app_change,
        },
    }
