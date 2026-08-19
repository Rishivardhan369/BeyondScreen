"""Deterministic Mobile Analytics assessment and historical aggregation."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from services.screen_time_parser import normalize_mobile_analytics

from .models import ActionableInputFeedback, DigitalSummary, GoalRescueOutcome, MomentumEntry, UserGoal
from .services import build_goal_health, build_goal_progress

ASSESSMENT_VERSION = 1

CATEGORY_ALIASES = {
    "social": "Social", "social networking": "Social", "entertainment": "Entertainment",
    "productivity": "Productivity", "education": "Education", "communication": "Communication",
    "games": "Games", "gaming": "Games", "browser": "Browser", "utilities": "Utilities",
    "other": "Other", "unknown": "Unknown",
}
KNOWN_APP_CATEGORIES = {
    "instagram": "Social", "facebook": "Social", "tiktok": "Social", "x": "Social",
    "twitter": "Social", "reddit": "Social", "netflix": "Entertainment", "youtube": "Entertainment",
    "spotify": "Entertainment", "whatsapp": "Communication", "messages": "Communication",
    "telegram": "Communication", "gmail": "Communication", "outlook": "Communication",
    "duolingo": "Education", "khan academy": "Education", "kindle": "Education",
    "notion": "Productivity", "todoist": "Productivity", "calendar": "Productivity",
    "chrome": "Browser", "safari": "Browser", "firefox": "Browser", "settings": "Utilities",
}


def _round(value):
    return round(float(value), 1) if value is not None else None


def _category(app):
    supplied = str(app.get("category") or "").strip().casefold()
    if supplied in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[supplied]
    return KNOWN_APP_CATEGORIES.get(str(app.get("name", "")).strip().casefold(), "Unknown")


def build_mobile_analytics_snapshot(*, parsed=None, manual_total=None, manual_metrics=None, report_date=None):
    payload = dict(parsed or {})
    if payload.get("total_minutes") is None and manual_total is not None:
        payload["total_minutes"] = manual_total
    for key, value in (manual_metrics or {}).items():
        if value is not None and payload.get(key) is None:
            payload[key] = value
    if not parsed:
        payload.update({"source_type": "manual", "platform": "unknown", "detection_confidence": "manual"})
    if report_date:
        payload["report_date"] = report_date.isoformat()
    return normalize_mobile_analytics(payload)


def data_quality_for(analytics):
    source = analytics.get("source_type")
    apps = bool(analytics.get("apps"))
    interaction_count = sum(analytics.get(key) is not None for key in ("pickups", "notifications", "sessions", "longest_session_minutes"))
    if source == "manual" and not apps and not interaction_count:
        return "Manual only"
    if analytics.get("total_minutes") is not None and apps and interaction_count >= 2:
        return "Complete"
    if analytics.get("total_minutes") is not None and (apps or interaction_count):
        return "Partial"
    return "Limited"


def _daily_history(user, before, days=30):
    start = before - timedelta(days=days)
    rows = DigitalSummary.objects.filter(user=user, created_at__date__gte=start, created_at__date__lt=before).values(
        "created_at", "screen_time_minutes", "mobile_analytics_snapshot"
    )
    grouped = defaultdict(list)
    for row in rows:
        local_day = timezone.localtime(row["created_at"]).date()
        analytics = row["mobile_analytics_snapshot"] or {}
        grouped[local_day].append({"total_minutes": analytics.get("total_minutes", row["screen_time_minutes"]), **analytics})
    daily = []
    for day, reports in grouped.items():
        item = {"date": day}
        for field in ("total_minutes", "pickups", "notifications", "sessions", "longest_session_minutes"):
            values = [report.get(field) for report in reports if report.get(field) is not None]
            item[field] = sum(values) / len(values) if values else None
        daily.append(item)
    return sorted(daily, key=lambda item: item["date"])


def _comparison(values, current):
    if len(values) < 2:
        return {"available": False, "sample_count": len(values)}
    average = sum(values) / len(values)
    difference = current - average
    return {
        "available": True, "average": _round(average), "difference": _round(difference),
        "percentage_difference": _round(difference / average * 100) if average else None,
        "sample_count": len(values),
        "direction": "above" if difference > 5 else "below" if difference < -5 else "close",
    }


def _metric_history_comparison(daily, field, current):
    recent = [item[field] for item in daily[-7:] if item.get(field) is not None]
    if current is None or len(recent) < 2:
        return {"available": False, "sample_count": len(recent)}
    return _comparison(recent, current)


def _distribution(analytics):
    apps = sorted(analytics.get("apps", []), key=lambda app: app.get("minutes", 0), reverse=True)
    total = analytics.get("total_minutes") or sum(app.get("minutes", 0) for app in apps)
    display_apps = [
        {
            **app,
            "category": _category(app),
            "share": _round(min(100, app.get("minutes", 0) / total * 100)) if total else None,
        }
        for app in apps
    ]
    top_total = sum(app.get("minutes", 0) for app in apps[:3])
    top_share = min(100, apps[0].get("minutes", 0) / total * 100) if apps and total else None
    top_three_share = min(100, top_total / total * 100) if total else None
    concentration = None
    if top_share is not None:
        concentration = "Distributed" if top_share < 40 else "Moderately concentrated" if top_share < 65 else "Highly concentrated"
    categories = defaultdict(int)
    for app in apps:
        categories[_category(app)] += app.get("minutes", 0)
    category_rows = [
        {"category": name, "minutes": minutes, "share": _round(min(100, minutes / total * 100)) if total else None}
        for name, minutes in sorted(categories.items(), key=lambda item: item[1], reverse=True)
    ]
    return {
        "top_app": display_apps[0] if display_apps else None, "top_apps": display_apps[:3], "top_app_share": _round(top_share),
        "top_three_share": _round(top_three_share), "significant_app_count": sum(app.get("minutes", 0) >= 15 or (total and app.get("minutes", 0) / total >= .1) for app in apps),
        "concentration": concentration, "categories": category_rows, "most_used_category": category_rows[0] if category_rows else None,
    }


def _goal_context(user, now):
    goal = UserGoal.objects.filter(user=user, is_primary=True, status=UserGoal.STATUS_ACTIVE).prefetch_related("actions").first()
    if not goal:
        return {"available": False, "message": "Create Goal DNA to connect your usage patterns to a concrete next step."}
    entries = list(MomentumEntry.objects.filter(user=user, goal=goal).select_related("action"))
    outcomes = list(GoalRescueOutcome.objects.filter(user=user, goal=goal))
    progress = build_goal_progress(goal, entries, today=now.date())
    health = build_goal_health(goal, progress, entries, outcomes, as_of=now)
    remaining = max(Decimal("0"), Decimal(goal.weekly_target) - Decimal(progress["current_week_progress"]))
    recent_sizes = [entry.action_size for entry in entries if entry.completed_at >= now - timedelta(days=30)]
    return {
        "available": True, "goal_id": goal.id, "goal_title": goal.title, "progress_unit": goal.progress_unit,
        "weekly_target": float(goal.weekly_target), "weekly_progress": float(progress["current_week_progress"]),
        "weekly_remaining": float(remaining), "goal_health": health.get("label", health.get("status", "No activity yet")),
        "actions": [{"id": action.id, "size": action.size, "title": action.title, "duration_minutes": action.duration_minutes, "progress_value": float(action.progress_value)} for action in goal.actions.all()],
        "recent_momentum_sizes": recent_sizes,
    }


def _feedback_preferences(user):
    rows = ActionableInputFeedback.objects.filter(user=user).values("input_type").annotate(
        total=Count("id"), positive=Count("id", filter=Q(outcome__in=["helpful", "used"]))
    )
    return {row["input_type"]: row["positive"] for row in rows if row["total"] >= 3}


def _actionable_inputs(user, analytics, baseline, distribution, goal, signals):
    candidates = []
    actions = sorted(goal.get("actions", []), key=lambda action: action["duration_minutes"])
    personal_target = user.userprofile.preferred_daily_screen_time_minutes
    total_minutes = analytics.get("total_minutes")
    if personal_target and total_minutes is not None and total_minutes > personal_target:
        action = next((item for item in actions if item["duration_minutes"] <= total_minutes - personal_target), None)
        candidates.append((105, {"id": "personal-target", "type": "personal_target", "title": "Use your personal screen-time target", "explanation": f"This report is {total_minutes - personal_target} minutes above your {personal_target}-minute personal target.", "source_signal": "User-defined daily screen-time target", "recommended_action": action["title"] if action else "Use the difference as a small reclaim opportunity", "estimated_duration_minutes": action["duration_minutes"] if action else None, "goal_id": goal.get("goal_id"), "goal_title": goal.get("goal_title"), "priority": "high", "why": "Based only on the daily target you set in Profile."}))
    if goal.get("available") and goal.get("weekly_remaining", 0) > 0 and actions:
        action = min(actions, key=lambda item: abs(item["progress_value"] - goal["weekly_remaining"]))
        candidates.append((100, {"id": "goal-target", "type": "goal_target", "title": "Close the gap to this week's target", "explanation": f"You have {goal['weekly_remaining']:g} {goal['progress_unit']} remaining this week.", "source_signal": "Current weekly Goal target", "recommended_action": action["title"], "estimated_duration_minutes": action["duration_minutes"], "goal_id": goal["goal_id"], "goal_title": goal["goal_title"], "priority": "high", "why": "Based on your current weekly Goal target."}))
    top = distribution.get("top_app")
    if top and goal.get("available") and actions:
        conservative = max(5, min(30, int(top["minutes"] * .2)))
        fitting = [action for action in actions if action["duration_minutes"] <= conservative]
        if fitting:
            action = fitting[-1]
            candidates.append((90, {"id": "app-concentration", "type": "app_concentration", "title": "Redirect a small part of recorded app time", "explanation": f"{top['name']} represented {distribution['top_app_share']:g}% of this report. Redirect {action['duration_minutes']} minutes toward your {action['size'].title()} step.", "source_signal": "Top-app concentration", "recommended_action": action["title"], "estimated_duration_minutes": action["duration_minutes"], "goal_id": goal["goal_id"], "goal_title": goal["goal_title"], "priority": "high", "why": "Based on today's top-app concentration; app purpose is treated as unknown."}))
    sizes = goal.get("recent_momentum_sizes", [])
    if goal.get("available") and len(sizes) >= 3 and actions:
        common = Counter(sizes).most_common(1)[0]
        if common[1] >= 2:
            action = next((item for item in actions if item["size"] == common[0]), actions[0])
            candidates.append((80, {"id": "momentum-continuation", "type": "momentum", "title": "Continue a proven Momentum pattern", "explanation": f"You recently completed {common[1]} {common[0].title()} actions.", "source_signal": "Recent Momentum completions", "recommended_action": action["title"], "estimated_duration_minutes": action["duration_minutes"], "goal_id": goal["goal_id"], "goal_title": goal["goal_title"], "priority": "medium", "why": "Based on your recent completed action sizes."}))
    for metric, title, recommendation in (("pickups", "Protect one uninterrupted block", "Try one notification-free Goal session."), ("notifications", "Reduce interruptions for the next step", "Silence optional notifications during your Goal step.")):
        comparison = baseline.get(metric, {})
        if comparison.get("direction") == "above":
            candidates.append((70, {"id": f"{metric}-baseline", "type": metric, "title": title, "explanation": f"This report recorded {analytics.get(metric)} {metric}, above your recent recorded average.", "source_signal": f"{metric.title()} compared with your own history", "recommended_action": recommendation, "estimated_duration_minutes": None, "goal_id": goal.get("goal_id"), "goal_title": goal.get("goal_title"), "priority": "medium", "why": f"Based on reliable {metric} counts in this report and at least two historical reports."}))
    if not goal.get("available"):
        candidates.append((95, {"id": "create-goal-context", "type": "goal_context", "title": "Connect this pattern to a goal", "explanation": goal["message"], "source_signal": "No active primary Goal DNA", "recommended_action": "Create your first Goal DNA", "estimated_duration_minutes": None, "goal_id": None, "goal_title": None, "priority": "high", "why": "Mobile Analytics is available, but only an active primary goal can guide Goal Rescue."}))
    if not candidates:
        candidates.append((40, {"id": "recorded-baseline", "type": "baseline", "title": "Keep building your personal baseline", "explanation": "Another reliable report will make comparisons more useful.", "source_signal": "Limited comparable history", "recommended_action": "Record your next phone analytics report", "estimated_duration_minutes": None, "goal_id": goal.get("goal_id"), "goal_title": goal.get("goal_title"), "priority": "low", "why": "Based on the amount of reliable history currently available."}))
    feedback = _feedback_preferences(user)
    candidates.sort(key=lambda pair: (pair[0] + min(feedback.get(pair[1]["type"], 0), 5), pair[1]["id"]), reverse=True)
    return [item for _, item in candidates[:3]]


def build_mobile_insights(user, *, today=None):
    today = today or timezone.localdate()
    summaries = list(DigitalSummary.objects.filter(user=user, created_at__date__gte=today - timedelta(days=89)).only("created_at", "screen_time_minutes", "mobile_analytics_snapshot", "mobile_assessment_snapshot"))
    metric_rows = {}
    for field in ("pickups", "notifications", "longest_session_minutes"):
        applicable = [(timezone.localtime(item.created_at).date(), (item.mobile_analytics_snapshot or {}).get(field)) for item in summaries]
        applicable = [(day, value) for day, value in applicable if value is not None]
        recent = [value for day, value in applicable if day >= today - timedelta(days=29)]
        prior = [value for day, value in applicable if today - timedelta(days=59) <= day < today - timedelta(days=29)]
        direction = "insufficient"
        change = None
        if len(recent) >= 2 and len(prior) >= 2:
            recent_average, prior_average = sum(recent) / len(recent), sum(prior) / len(prior)
            change = _round(recent_average - prior_average)
            direction = "higher" if change > 0 else "lower" if change < 0 else "steady"
        metric_rows[field] = {"average": _round(sum(recent) / len(recent)) if recent else None, "samples": len(recent), "direction": direction, "change": change}
    concentrations, categories, qualities, sources = [], Counter(), Counter(), Counter()
    for item in summaries:
        snapshot = item.mobile_analytics_snapshot or {}
        qualities[data_quality_for(snapshot)] += 1
        sources[snapshot.get("source_type", "unknown")] += 1
        distribution = _distribution(snapshot)
        if distribution.get("top_app_share") is not None:
            concentrations.append(distribution["top_app_share"])
        for row in distribution.get("categories", []):
            categories[row["category"]] += row["minutes"]
    return {"report_count": len(summaries), "metrics": metric_rows, "average_concentration": _round(sum(concentrations) / len(concentrations)) if concentrations else None, "category_distribution": [{"category": key, "minutes": value} for key, value in categories.most_common()], "data_quality": dict(qualities), "sources": dict(sources), "minimum_sample_note": "Directions require at least two reliable reports in each 30-day comparison period."}


def build_mobile_analytics_assessment(summary):
    analytics = summary.mobile_analytics_snapshot or build_mobile_analytics_snapshot(manual_total=summary.screen_time_minutes, report_date=timezone.localdate(summary.created_at))
    as_of = timezone.localtime(summary.created_at)
    daily = _daily_history(summary.user, as_of.date())
    recent_7 = [item["total_minutes"] for item in daily if item["date"] >= as_of.date() - timedelta(days=7) and item.get("total_minutes") is not None]
    recent_30 = [item["total_minutes"] for item in daily if item.get("total_minutes") is not None]
    baseline = {
        "seven_day": _comparison(recent_7, analytics.get("total_minutes", summary.screen_time_minutes)),
        "thirty_day": _comparison(recent_30, analytics.get("total_minutes", summary.screen_time_minutes)),
    }
    for field in ("pickups", "notifications", "sessions", "longest_session_minutes"):
        baseline[field] = _metric_history_comparison(daily, field, analytics.get(field))
    distribution = _distribution(analytics)
    signals = []
    if baseline["seven_day"].get("available"):
        signals.append({"type": "baseline", "label": f"{baseline['seven_day']['direction'].title()} recent baseline", "explanation": f"This report is {abs(baseline['seven_day']['difference']):g} minutes {baseline['seven_day']['direction']} your recent recorded average."})
    else:
        signals.append({"type": "insufficient_data", "label": "Personal baseline still building", "explanation": "There are not enough earlier recorded days for a reliable comparison."})
    if distribution.get("concentration"):
        signals.append({"type": "concentration", "label": distribution["concentration"], "explanation": f"The highest recorded app represented {distribution['top_app_share']:g}% of recorded screen time."})
    for field in ("pickups", "notifications"):
        comparison = baseline[field]
        if comparison.get("direction") == "above":
            signals.append({"type": field, "label": f"{field.title()} above personal baseline", "explanation": f"Compared with {comparison['sample_count']} reliable historical recorded days."})
    longest = analytics.get("longest_session_minutes")
    if longest is not None:
        signals.append({"type": "session", "label": "Longest session recorded", "explanation": f"The longest recorded session was {longest} minutes."})
    last_use = analytics.get("last_use_time")
    if last_use:
        late = False
        for clock_format in ("%I:%M %p", "%H:%M"):
            try:
                hour = datetime.strptime(last_use, clock_format).hour
                late = hour >= 23 or hour < 5
                break
            except ValueError:
                continue
        signals.append({"type": "late_use" if late else "timing", "label": "Late recorded use" if late else "Latest recorded use", "explanation": f"The latest recorded use was {last_use}."})
    goal = _goal_context(summary.user, as_of)
    inputs = _actionable_inputs(summary.user, analytics, baseline, distribution, goal, signals)
    quality = data_quality_for(analytics)
    if quality in ("Manual only", "Limited"):
        concise = "Only limited mobile analytics were available, so this assessment focuses on recorded screen time."
    elif baseline["seven_day"].get("available") and distribution.get("concentration"):
        concise = f"This report was {baseline['seven_day']['direction']} your recent recorded average and usage was {distribution['concentration'].lower()}."
    else:
        concise = f"This {quality.lower()} assessment uses the reliable mobile analytics available in this report."
    total = analytics.get("total_minutes", summary.screen_time_minutes)
    hours = total / 60 if total else None
    interactions = {key: analytics.get(key) for key in ("pickups", "notifications", "sessions", "longest_session_minutes", "first_use_time", "last_use_time")}
    interactions["average_session_minutes"] = _round(total / analytics["sessions"]) if analytics.get("sessions") else None
    interactions["pickups_per_screen_hour"] = _round(analytics["pickups"] / hours) if analytics.get("pickups") is not None and hours else None
    interactions["notifications_per_screen_hour"] = _round(analytics["notifications"] / hours) if analytics.get("notifications") is not None and hours else None
    return {
        "assessment_version": ASSESSMENT_VERSION, "data_quality": quality, "source": analytics.get("source_type", "unknown"),
        "summary": concise, "screen_time": {"total_minutes": total, "comparison": baseline},
        "app_patterns": distribution, "interaction_metrics": interactions,
        "usage_signals": signals, "goal_context": goal, "actionable_inputs": inputs,
    }


def build_transient_mobile_assessment(analytics):
    """Create a non-persisted factual assessment for an anonymous report."""
    distribution = _distribution(analytics)
    quality = data_quality_for(analytics)
    signals = [{"type": "insufficient_data", "label": "Personal baseline unavailable", "explanation": "Sign in and record reports over time to compare against your own history."}]
    if distribution.get("concentration"):
        signals.append({"type": "concentration", "label": distribution["concentration"], "explanation": f"The highest recorded app represented {distribution['top_app_share']:g}% of recorded screen time."})
    return {
        "assessment_version": ASSESSMENT_VERSION, "data_quality": quality,
        "source": analytics.get("source_type", "unknown"),
        "summary": "This assessment uses only the reliable metrics in this report; sign in to add personal history and Goal context.",
        "screen_time": {"total_minutes": analytics.get("total_minutes"), "comparison": {"seven_day": {"available": False}}},
        "app_patterns": distribution,
        "interaction_metrics": {key: analytics.get(key) for key in ("pickups", "notifications", "sessions", "longest_session_minutes", "first_use_time", "last_use_time")},
        "usage_signals": signals,
        "goal_context": {"available": False, "message": "Sign in and create Goal DNA to connect this report to a concrete next step."},
        "actionable_inputs": [{"id": "sign-in-goal-context", "type": "goal_context", "title": "Add personal context", "explanation": "Your factual Mobile Analytics are ready. Personal baselines and goal-aware actions require an account.", "source_signal": "No signed-in Goal context", "recommended_action": "Sign in or create an account", "estimated_duration_minutes": None, "goal_id": None, "goal_title": None, "priority": "high", "why": "Based on the absence of a signed-in personal history."}],
    }


def build_weekly_mobile_analytics(user, week_start, week_end):
    summaries = list(DigitalSummary.objects.filter(user=user, created_at__date__range=(week_start, week_end)).only("screen_time_minutes", "mobile_analytics_snapshot", "mobile_assessment_snapshot"))
    if not summaries:
        return {"available": False, "report_count": 0}
    snapshots = [summary.mobile_analytics_snapshot or {} for summary in summaries]
    def average(field):
        values = [snapshot.get(field) for snapshot in snapshots if snapshot.get(field) is not None]
        return _round(sum(values) / len(values)) if values else None
    app_totals, input_types, qualities, sources = Counter(), Counter(), Counter(), Counter()
    for summary, snapshot in zip(summaries, snapshots):
        qualities[data_quality_for(snapshot)] += 1
        sources[snapshot.get("source_type", "unknown")] += 1
        for app in snapshot.get("apps", []):
            app_totals[app.get("name", "Unknown")] += app.get("minutes", 0)
        for item in (summary.mobile_assessment_snapshot or {}).get("actionable_inputs", []):
            input_types[item.get("type", "unknown")] += 1
    return {"available": True, "report_count": len(summaries), "average_screen_time": _round(sum(summary.screen_time_minutes for summary in summaries) / len(summaries)), "average_pickups": average("pickups"), "average_notifications": average("notifications"), "top_app": app_totals.most_common(1)[0][0] if app_totals else None, "most_common_input_type": input_types.most_common(1)[0][0] if input_types else None, "data_quality": dict(qualities), "sources": dict(sources)}
