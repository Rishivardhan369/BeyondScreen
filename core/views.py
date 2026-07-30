from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from django.http import HttpResponse, Http404
from django.db.models import Avg, Q
from django.db import transaction
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import os
from django.conf import settings
from .models import (
    DigitalSummary,
    GoalAction,
    Postcard,
    UserGoal,
    UserProfile,
)
from .forms import (
    GoalDNAForm,
    PostcardForm,
    SignUpForm,
    UserLoginForm,
    UserProfileForm,
)
from .services import (
    build_goal_rescue,
    format_screen_time,
    generate_postcard,
    render_postcard_pdf,
    render_postcard_png,
)
from services.screen_time_parser import parse_screen_time_report
from datetime import date, time


@login_required
def goal_onboarding(request):
    existing_primary = UserGoal.objects.filter(
        user=request.user,
        status=UserGoal.STATUS_ACTIVE,
        is_primary=True,
    ).exists()

    if existing_primary:
        messages.info(
            request,
            "Your primary goal is already active.",
        )
        return redirect("core:dashboard")

    if request.method == "POST":
        form = GoalDNAForm(request.POST)
        if form.is_valid():
            request.session["pending_goal_dna"] = (
                form.to_session_data()
            )
            return redirect("core:goal_confirmation")
    else:
        form = GoalDNAForm()

    return render(
        request,
        "goals/onboarding.html",
        {"form": form},
    )


@login_required

@login_required
def goal_confirmation(request):
    pending_goal = request.session.get("pending_goal_dna")

    if not pending_goal:
        messages.info(
            request,
            "Create your primary goal before confirming it.",
        )
        return redirect("core:goal_onboarding")

    if request.method == "POST":
        existing_primary = UserGoal.objects.filter(
            user=request.user,
            status=UserGoal.STATUS_ACTIVE,
            is_primary=True,
        ).exists()

        if existing_primary:
            request.session.pop("pending_goal_dna", None)
            messages.info(
                request,
                "Your primary goal is already active.",
            )
            return redirect("core:dashboard")

        with transaction.atomic():
            goal = UserGoal(
                user=request.user,
                title=pending_goal["title"],
                why_it_matters=pending_goal[
                    "why_it_matters"
                ],
                current_focus=pending_goal.get(
                    "current_focus",
                    "",
                ),
                progress_unit=pending_goal[
                    "progress_unit"
                ],
                weekly_target=pending_goal[
                    "weekly_target"
                ],
                preferred_days=pending_goal.get(
                    "preferred_days",
                    [],
                ),
                preferred_time=(
                    time.fromisoformat(
                        pending_goal["preferred_time"]
                    )
                    if pending_goal.get("preferred_time")
                    else None
                ),
                deadline=(
                    date.fromisoformat(
                        pending_goal["deadline"]
                    )
                    if pending_goal.get("deadline")
                    else None
                ),
                is_primary=True,
                status=UserGoal.STATUS_ACTIVE,
            )
            goal.full_clean()
            goal.save()

            for size in (
                GoalAction.SIZE_MINIMUM,
                GoalAction.SIZE_STANDARD,
                GoalAction.SIZE_DEEP,
            ):
                action_data = pending_goal["actions"][size]
                action = GoalAction(
                    goal=goal,
                    size=size,
                    title=action_data["title"],
                    duration_minutes=action_data[
                        "duration_minutes"
                    ],
                    progress_value=action_data[
                        "progress_value"
                    ],
                )
                action.full_clean()
                action.save()

        request.session.pop("pending_goal_dna", None)
        messages.success(
            request,
            "Your primary goal is now active.",
        )
        return redirect("core:dashboard")

    display_goal = pending_goal.copy()

    preferred_time = pending_goal.get("preferred_time")
    if preferred_time:
        parsed_time = time.fromisoformat(preferred_time)
        display_goal["preferred_time_display"] = (
            parsed_time.strftime("%I:%M %p").lstrip("0")
        )
    else:
        display_goal["preferred_time_display"] = (
            "Any time that works"
        )

    deadline = pending_goal.get("deadline")
    if deadline:
        parsed_deadline = date.fromisoformat(deadline)
        display_goal["deadline_display"] = (
            f"{parsed_deadline.day} "
            f"{parsed_deadline.strftime('%B %Y')}"
        )
    else:
        display_goal["deadline_display"] = (
            "No deadline selected"
        )

    return render(
        request,
        "goals/confirmation.html",
        {"goal": display_goal},
    )


def home(request):
    if request.method == "POST":
        form = PostcardForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            filename = data["file"].name if data["file"] else None
            # Try to extract screen time from uploaded file using OCR
            ocr_result = None
            minutes_from_ocr = None
            if data["file"]:
                ocr_result = parse_screen_time_report(data["file"])
                if ocr_result and isinstance(ocr_result, dict):
                    minutes_from_ocr = ocr_result.get("total_screen_time")
                else:
                    # OCR unavailable or failed
                    messages.info(request, "Automatic report parsing is currently unavailable. Please enter your screen time manually.")
            # Determine minutes to use: OCR if successful, else form input
            if minutes_from_ocr is not None:
                minutes = minutes_from_ocr
                # Optionally store OCR apps data in session for future use
                if ocr_result.get("apps"):
                    request.session["ocr_apps"] = ocr_result["apps"]
            else:
                # Fallback to manual input
                screen_time_minutes = data.get("screen_time")
                try:
                    minutes = int(screen_time_minutes) if screen_time_minutes is not None else 0
                except (ValueError, TypeError):
                    minutes = 0
                # Clear any existing OCR apps data
                if "ocr_apps" in request.session:
                    del request.session["ocr_apps"]

            postcard_data = generate_postcard(
                mood=data["mood"],
                goal=data["goal"],
                screen_time=format_screen_time(str(minutes)),
                has_report=bool(filename),
            )
            postcard_data["filename"] = filename
            request.session["postcard"] = postcard_data

            # Calculate wellness data
            if minutes < 120:
                wellness_score = 100
            elif minutes < 240:
                wellness_score = 90
            elif minutes < 360:
                wellness_score = 75
            elif minutes < 480:
                wellness_score = 60
            elif minutes < 600:
                wellness_score = 40
            else:
                wellness_score = 20

            if wellness_score >= 90:
                category = "Excellent"
            elif wellness_score >= 75:
                category = "Very Good"
            elif wellness_score >= 60:
                category = "Good"
            elif wellness_score >= 40:
                category = "Moderate"
            elif wellness_score >= 20:
                category = "Needs Attention"
            else:
                category = "Critical"

            # Insight
            if wellness_score >= 90:
                insight = "Your screen time is well within healthy limits."
            elif wellness_score >= 75:
                insight = "Your screen time is moderate but could be improved."
            elif wellness_score >= 60:
                insight = "You are spending a considerable amount of time on screens."
            elif wellness_score >= 40:
                insight = "Your screen time is high and may affect wellbeing."
            elif wellness_score >= 20:
                insight = "Your screen time is very high; consider taking breaks."
            else:
                insight = "Your screen time is excessive; urgent reduction is advised."

            # Recommendation
            if minutes < 120:
                recommendation = "Keep up the great work! Maintain your current habits."
            elif minutes < 240:
                recommendation = "Try to limit recreational screen time to under 2 hours daily."
            elif minutes < 360:
                recommendation = "Consider setting specific times for checking social media."
            elif minutes < 480:
                recommendation = "Implement regular screen-free periods during your day."
            elif minutes < 600:
                recommendation = "Set a daily screen time limit and use device reminders."
            else:
                recommendation = "Seek support to reduce screen time; consider digital detox days."

            # Motivational
            if wellness_score >= 75:
                motivational = "Small changes today lead to big improvements tomorrow."
            elif wellness_score >= 40:
                motivational = "Every minute you reclaim is a minute for what truly matters."
            else:
                motivational = "You have the power to reshape your digital habits."

            # Format total screen time for display (hours and minutes)
            hours = minutes // 60
            mins = minutes % 60
            if hours > 0:
                total_screen_time_display = f"{hours}h {mins:02d}m"
            else:
                total_screen_time_display = f"{mins}m"

            # Store summary data in session
            opportunity_cost = {
                "reading_pages": int(minutes * (20 / 60)),
                "walking_km": int(minutes * (5 / 60)),
                "gym_sessions": int(minutes / 60),
                "pomodoro_sessions": int(minutes / 25),
                "meditation_sessions": int(minutes / 10),
                "sleep_hours": int(minutes / 60),
            }
            request.session["summary_data"] = {
                "screen_time_minutes": minutes,
                "total_screen_time": total_screen_time_display,
                "wellness_score": wellness_score,
                "wellness_category": category,
                "insight": insight,
                "recommendation": recommendation,
                "motivational": motivational,
                "opportunity_cost": opportunity_cost,
            }

            # If user is authenticated, save a DigitalSummary record
            if request.user.is_authenticated:
                DigitalSummary.objects.create(
                    user=request.user,
                    screen_time_minutes=minutes,
                    wellness_score=wellness_score,
                    category=category,
                    insight=insight,
                )

            messages.success(request, "Postcard generated!")
            return redirect("core:summary")
    else:
        form = PostcardForm()

    return render(request, "home.html", {"form": form})



def summary(request):
    summary_data = request.session.get("summary_data")

    if not summary_data:
        messages.info(
            request,
            "No summary data available. Please generate a postcard first.",
        )
        return redirect("core:home")

    display_summary = dict(summary_data)
    screen_time_minutes = display_summary.get(
        "screen_time_minutes",
        0,
    )
    display_summary["goal_rescue"] = build_goal_rescue(
        request.user,
        screen_time_minutes,
    )

    return render(
        request,
        "summary.html",
        {"summary": display_summary},
    )



def download_postcard(request, file_format):
    postcard_data = request.session.get("postcard")
    if not postcard_data:
        messages.error(request, "No postcard data found. Please generate a postcard first.")
        return redirect("core:home")

    if file_format == "pdf":
        content = render_postcard_pdf(postcard_data)
        content_type = "application/pdf"
        filename = "beyondscreen-postcard.pdf"
    elif file_format == "png":
        content = render_postcard_png(postcard_data)
        content_type = "image/png"
        filename = "beyondscreen-postcard.png"
    else:
        messages.error(request, "Invalid format specified.")
        return redirect("core:summary")

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def dashboard(request):
    def get_created_value(item):
        return (
            getattr(item, "created_date", None)
            or getattr(item, "created_at", None)
        )

    def get_created_sort_key(item):
        created_value = get_created_value(item)

        if created_value is None:
            return (0, 0, 0, 0, 0, 0)

        return (
            created_value.year,
            created_value.month,
            created_value.day,
            getattr(created_value, "hour", 0),
            getattr(created_value, "minute", 0),
            getattr(created_value, "second", 0),
        )

    def format_minutes(minutes):
        try:
            minutes = max(0, int(minutes))
        except (TypeError, ValueError):
            minutes = 0

        hours, remaining_minutes = divmod(minutes, 60)

        if hours:
            return f"{hours}h {remaining_minutes:02d}m"

        return f"{remaining_minutes}m"

    def build_recommendation(minutes):
        if minutes < 120:
            return "Keep up the great work! Maintain your current habits."
        if minutes < 240:
            return "Try to limit recreational screen time to under 2 hours daily."
        if minutes < 360:
            return "Consider setting specific times for checking social media."
        if minutes < 480:
            return "Implement regular screen-free periods during your day."
        if minutes < 600:
            return "Set a daily screen time limit and use device reminders."

        return "Seek support to reduce screen time; consider digital detox days."

    all_postcards = list(
        Postcard.objects.filter(user=request.user)
    )
    all_summaries = list(
        DigitalSummary.objects.filter(user=request.user)
    )

    all_postcards.sort(
        key=get_created_sort_key,
        reverse=True,
    )
    all_summaries.sort(
        key=get_created_sort_key,
        reverse=True,
    )

    recent_postcards = all_postcards[:5]
    recent_summaries = all_summaries[:5]
    latest_summary = all_summaries[0] if all_summaries else None

    session_summary = request.session.get("summary_data") or {}

    if latest_summary is not None:
        latest_minutes = max(
            0,
            int(latest_summary.screen_time_minutes or 0),
        )

        opportunity_cost = {
            "reading_pages": int(latest_minutes * (20 / 60)),
            "walking_km": int(latest_minutes * (5 / 60)),
            "gym_sessions": int(latest_minutes / 60),
            "pomodoro_sessions": int(latest_minutes / 25),
            "meditation_sessions": int(latest_minutes / 10),
            "sleep_hours": int(latest_minutes / 60),
        }

        dashboard_summary = {
            "total_screen_time": format_minutes(latest_minutes),
            "recommendation": build_recommendation(latest_minutes),
            "opportunity_cost": opportunity_cost,
        }
    else:
        dashboard_summary = {
            "total_screen_time": session_summary.get(
                "total_screen_time",
                "0m",
            ),
            "recommendation": session_summary.get(
                "recommendation",
                "Upload your first report to receive a recommendation.",
            ),
            "opportunity_cost": session_summary.get(
                "opportunity_cost",
                {
                    "reading_pages": 0,
                    "walking_km": 0,
                    "gym_sessions": 0,
                    "pomodoro_sessions": 0,
                    "meditation_sessions": 0,
                    "sleep_hours": 0,
                },
            ),
        }

    mood_definitions = [
        ("Happy", "bs-happy", "#f6bb55"),
        ("Calm", "bs-calm", "#42d6cf"),
        ("Neutral", "bs-neutral", "#3b8de8"),
        ("Stressed", "bs-stressed", "#e25270"),
        ("Tired", "bs-tired", "#9d4eb2"),
    ]

    mood_stats = []

    for label, css_class, color in mood_definitions:
        count = sum(
            1
            for postcard in all_postcards
            if str(postcard.mood).strip().lower() == label.lower()
        )

        mood_stats.append(
            {
                "label": label,
                "css_class": css_class,
                "color": color,
                "count": count,
                "percentage": 0,
            }
        )

    mood_report_count = sum(
        mood["count"]
        for mood in mood_stats
    )

    if mood_report_count:
        for mood in mood_stats:
            mood["percentage"] = round(
                mood["count"] * 100 / mood_report_count
            )

        gradient_segments = []
        gradient_start = 0.0

        for mood in mood_stats:
            if mood["count"] == 0:
                continue

            gradient_end = (
                gradient_start
                + mood["count"] * 100 / mood_report_count
            )

            gradient_segments.append(
                f'{mood["color"]} '
                f'{gradient_start:.2f}% '
                f'{gradient_end:.2f}%'
            )

            gradient_start = gradient_end

        mood_gradient = (
            "conic-gradient("
            + ", ".join(gradient_segments)
            + ")"
        )
    else:
        mood_gradient = "conic-gradient(#1a2a3a 0% 100%)"

    current_date = date.today()
    monthly_activity = []

    for months_back in range(5, -1, -1):
        total_month_number = (
            current_date.year * 12
            + current_date.month
            - 1
            - months_back
        )

        year, zero_based_month = divmod(
            total_month_number,
            12,
        )
        month = zero_based_month + 1

        count = 0

        for summary in all_summaries:
            created_value = get_created_value(summary)

            if (
                created_value is not None
                and created_value.year == year
                and created_value.month == month
            ):
                count += 1

        monthly_activity.append(
            {
                "label": date(year, month, 1).strftime("%b"),
                "count": count,
                "height": 0,
            }
        )

    highest_month_count = max(
        (
            month["count"]
            for month in monthly_activity
        ),
        default=0,
    )

    for month in monthly_activity:
        if highest_month_count == 0:
            month["height"] = 0
        elif month["count"] == 0:
            month["height"] = 0
        else:
            month["height"] = max(
                10,
                round(
                    month["count"] * 100 / highest_month_count
                )
            )

    recent_reports = []

    for index, summary in enumerate(recent_summaries[:4]):
        recent_reports.append(
            {
                "created": get_created_value(summary),
                "is_latest": index == 0,
                "screen_time": format_minutes(
                    summary.screen_time_minutes
                ),
            }
        )

    context = {
        "recent_postcards": recent_postcards,
        "recent_summaries": recent_summaries,
        "recent_reports": recent_reports,
        "dashboard_summary": dashboard_summary,
        "mood_stats": mood_stats,
        "mood_report_count": mood_report_count,
        "mood_gradient": mood_gradient,
        "monthly_activity": monthly_activity,
    }

    return render(request, "dashboard.html", context)


@login_required
def delete_postcard(request, postcard_id):
    postcard = get_object_or_404(Postcard, id=postcard_id, user=request.user)
    if request.method == "POST":
        postcard.delete()
        messages.success(request, "Postcard deleted successfully.")
        return redirect("core:postcard_history")
    return render(request, "postcard_history.html", {"postcard": postcard})


@login_required
def view_postcard(request, postcard_id):
    postcard = get_object_or_404(Postcard, id=postcard_id, user=request.user)
    return render(request, "view_postcard.html", {"postcard": postcard})


@login_required
def profile(request):
    user_profile, created = UserProfile.objects.get_or_create(user=request.user)
    return render(request, "registration/profile.html", {"user_profile": user_profile})


@login_required
def edit_profile(request):
    user_profile, created = UserProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = UserProfileForm(request.POST, request.FILES, instance=user_profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully.")
            return redirect("core:profile")
    else:
        form = UserProfileForm(instance=user_profile)

    return render(request, "registration/edit_profile.html", {"form": form})


@login_required
def change_password(request):
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # Important to keep user logged in
            messages.success(request, "Password changed successfully.")
            return redirect("core:profile")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = PasswordChangeForm(request.user)

    return render(request, "registration/change_password.html", {"form": form})


def user_login(request):
    if request.method == "POST":
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get("username")
            password = form.cleaned_data.get("password")
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.info(request, f"You are now logged in as {username}.")
                return redirect("core:dashboard")
            else:
                messages.error(request, "Invalid username or password.")
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = UserLoginForm()

    return render(request, "registration/login.html", {"form": form})


def register(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            username = form.cleaned_data.get("username")
            messages.success(request, f"Account created for {username}!")
            login(request, user)
            return redirect("core:dashboard")
        else:
            for msg in form.error_messages:
                messages.error(request, f"{msg}: {form.error_messages[msg]}")

    else:
        form = SignUpForm()

    return render(request, "registration/register.html", {"form": form})


def user_logout(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("core:home")


@login_required
def history(request):
    base_summaries = DigitalSummary.objects.filter(user=request.user)

    total_reports = base_summaries.count()
    avg_wellness = (
        base_summaries.aggregate(average=Avg("wellness_score"))["average"]
        or 0
    )
    latest_summary = base_summaries.order_by("-created_at").first()

    categories = list(
        base_summaries.order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )

    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "all").strip()
    sort = request.GET.get("sort", "newest").strip()

    summaries = base_summaries

    if query:
        summaries = summaries.filter(
            Q(insight__icontains=query)
            | Q(category__icontains=query)
        )

    if category != "all":
        summaries = summaries.filter(category=category)

    sort_fields = {
        "newest": "-created_at",
        "oldest": "created_at",
        "highest": "-wellness_score",
        "lowest": "wellness_score",
    }

    if sort not in sort_fields:
        sort = "newest"

    summaries = summaries.order_by(sort_fields[sort])

    def format_minutes(minutes):
        try:
            minutes = max(0, int(minutes))
        except (TypeError, ValueError):
            minutes = 0

        hours, remaining_minutes = divmod(minutes, 60)

        if hours:
            return f"{hours}h {remaining_minutes:02d}m"

        return f"{remaining_minutes}m"

    history_reports = []

    for summary_item in summaries:
        wellness_score = max(
            0,
            min(100, int(summary_item.wellness_score or 0)),
        )

        if wellness_score >= 70:
            wellness_tone = "good"
        elif wellness_score >= 40:
            wellness_tone = "steady"
        else:
            wellness_tone = "attention"

        history_reports.append(
            {
                "id": summary_item.id,
                "created_at": summary_item.created_at,
                "screen_time": format_minutes(
                    summary_item.screen_time_minutes
                ),
                "wellness_score": wellness_score,
                "wellness_tone": wellness_tone,
                "category": summary_item.category,
                "insight": summary_item.insight,
                "opportunity_cost": summary_item.opportunity_cost,
            }
        )

    latest_screen_time = (
        format_minutes(latest_summary.screen_time_minutes)
        if latest_summary
        else "0m"
    )

    context = {
        "history_reports": history_reports,
        "filtered_report_count": len(history_reports),
        "total_reports": total_reports,
        "avg_wellness": avg_wellness,
        "latest_summary": latest_summary,
        "latest_screen_time": latest_screen_time,
        "dashboard_summary": {
            "total_screen_time": latest_screen_time,
        },
        "categories": categories,
        "query": query,
        "category": category,
        "sort": sort,
    }

    return render(request, "history.html", context)

@login_required
def postcard_history(request):
    base_postcards = Postcard.objects.filter(user=request.user)

    total_postcards = base_postcards.count()
    latest_postcard = base_postcards.order_by("-created_at").first()

    moods = list(
        base_postcards.order_by("mood")
        .values_list("mood", flat=True)
        .distinct()
    )

    mood_counts = {}

    for mood_value in base_postcards.values_list("mood", flat=True):
        mood_key = str(mood_value or "").strip() or "Unknown"
        mood_counts[mood_key] = mood_counts.get(mood_key, 0) + 1

    dominant_mood = (
        max(mood_counts, key=mood_counts.get)
        if mood_counts
        else "None yet"
    )

    query = request.GET.get("q", "").strip()
    mood_filter = request.GET.get("mood", "All").strip() or "All"
    sort = request.GET.get("sort", "newest").strip()

    postcards = base_postcards

    if query:
        postcards = postcards.filter(
            Q(haiku__icontains=query)
            | Q(reflection__icontains=query)
            | Q(goal__icontains=query)
            | Q(pledge__icontains=query)
        )

    if mood_filter != "All":
        postcards = postcards.filter(mood=mood_filter)

    if sort == "oldest":
        postcards = postcards.order_by("created_at")
    else:
        sort = "newest"
        postcards = postcards.order_by("-created_at")

    postcard_cards = []

    for postcard in postcards:
        haiku_lines = [
            line.strip()
            for line in str(postcard.haiku or "").splitlines()
            if line.strip()
        ]

        postcard_cards.append(
            {
                "id": postcard.id,
                "mood": postcard.mood,
                "goal": postcard.goal,
                "screen_time": postcard.screen_time or "0m",
                "haiku_preview": (
                    haiku_lines[0]
                    if haiku_lines
                    else "A quieter moment begins here."
                ),
                "reflection": postcard.reflection,
                "created_at": postcard.created_at,
            }
        )

    latest_haiku_lines = (
        [
            line.strip()
            for line in str(latest_postcard.haiku or "").splitlines()
            if line.strip()
        ]
        if latest_postcard
        else []
    )

    context = {
        "postcard_cards": postcard_cards,
        "filtered_postcard_count": len(postcard_cards),
        "total_postcards": total_postcards,
        "latest_postcard": latest_postcard,
        "latest_screen_time": (
            latest_postcard.screen_time or "0m"
            if latest_postcard
            else "0m"
        ),
        "latest_haiku": (
            latest_haiku_lines[0]
            if latest_haiku_lines
            else "A quieter moment begins here."
        ),
        "dominant_mood": dominant_mood,
        "moods": moods,
        "query": query,
        "mood_filter": mood_filter,
        "sort": sort,
    }

    return render(request, "postcard_history.html", context)

@login_required
def view_summary(request, summary_id):
    summary = get_object_or_404(
        DigitalSummary,
        id=summary_id,
        user=request.user,
    )
    return render(request, "view_summary.html", {"summary": summary})

@login_required
def download_postcard_by_id(request, postcard_id, file_format):
    postcard = get_object_or_404(
        Postcard,
        id=postcard_id,
        user=request.user,
    )

    postcard_data = {
        "mood": postcard.mood,
        "goal": postcard.goal,
        "screen_time": postcard.screen_time or "0m",
        "has_report": postcard.has_report,
        "filename": postcard.filename,
        "haiku": postcard.haiku,
        "reflection": postcard.reflection,
        "action": postcard.action,
        "pledge": postcard.pledge,
    }

    if file_format == "pdf":
        content = render_postcard_pdf(postcard_data)
        content_type = "application/pdf"
        filename = f"beyondscreen-postcard-{postcard_id}.pdf"
    elif file_format == "png":
        content = render_postcard_png(postcard_data)
        content_type = "image/png"
        filename = f"beyondscreen-postcard-{postcard_id}.png"
    else:
        raise Http404("Format not supported")

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )
    return response
