from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q, Sum, Sum
from django.utils import timezone
from datetime import timedelta
from collections import defaultdict

from .forms import PostcardForm, SignUpForm, UserLoginForm, UserProfileForm
from .models import UserProfile, Postcard, DigitalSummary
from .services import (
    format_screen_time,
    generate_postcard,
    render_postcard_pdf,
    render_postcard_png,
)


def home(request):
    if request.method == "POST":
        form = PostcardForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            filename = data["file"].name if data["file"] else None
            screen_time_minutes = data.get("screen_time")
            try:
                minutes = int(screen_time_minutes) if screen_time_minutes is not None else 0
            except (ValueError, TypeError):
                minutes = 0

            postcard_data = generate_postcard(
                mood=data["mood"],
                goal=data["goal"],
                screen_time=format_screen_time(data.get("screen_time")),
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
            request.session["summary_data"] = {
                "total_screen_time": total_screen_time_display,
                "wellness_score": wellness_score,
                "wellness_category": category,
                "insight": insight,
                "recommendation": recommendation,
                "motivational": motivational,
            }

            # If user is authenticated, save a DigitalSummary record
            if request.user.is_authenticated:
                from .models import DigitalSummary
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
    """Display the digital summary page."""
    summary_data = request.session.get("summary_data")
    if not summary_data:
        # If no summary data (e.g., user arrived directly), redirect to home
        return redirect("core:home")
    return render(request, "summary.html", {"summary": summary_data})


def download_postcard(request, file_format):
    postcard = request.session.get("postcard")
    if not postcard:
        raise Http404("Generate a postcard before downloading it.")

    if file_format == "png":
        content = render_postcard_png(postcard)
        content_type = "image/png"
    elif file_format == "pdf":
        content = render_postcard_pdf(postcard)
        content_type = "application/pdf"
    else:
        raise Http404("Unsupported postcard format.")

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="unscroll-postcard.{file_format}"'
    return response


def register(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            username = form.cleaned_data.get("username")
            raw_password = form.cleaned_data.get("password1")
            login(request, user)
            messages.success(request, "Registration successful! Welcome to Unscroll.")
            # Send welcome email (optional)
            # send_welcome_email(user.email, username)
            return redirect("core:home")
    else:
        form = SignUpForm()
    return render(request, "registration/register.html", {"form": form})


def user_login(request):
    if request.method == "POST":
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, "Login successful!")
            # Redirect to the page the user was trying to access before login
            next_url = request.GET.get("next", "core:home")
            return redirect(next_url)
    else:
        form = UserLoginForm()
    return render(request, "registration/login.html", {"form": form})


@login_required
def user_logout(request):
    if request.method == "POST":
        logout(request)
        return redirect("core:home")
    else:
        # If GET, redirect to home (or could show a confirmation page)
        return redirect("core:home")


@login_required
def profile(request):
    user_profile = request.user.userprofile
    return render(request, "registration/profile.html", {"user_profile": user_profile})


@login_required
def edit_profile(request):
    user_profile = request.user.userprofile
    if request.form:
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
            update_session_auth_hash(request, user)
            messages.success(request, "Your password was successfully updated!")
            return redirect("core:profile")
    else:
        form = PasswordChangeForm(request.user)
    return render(request, "registration/change_password.html", {"form": form})


@login_required
def dashboard(request):
    # Get all postcards for the current user
    postcards_qs = Postcard.objects.filter(user=request.user)
    postcards = list(postcards_qs)  # Evaluate the queryset once

    # Calculate statistics
    total_postcards = len(postcards)

    # Calculate monthly postcards (current month)
    now = timezone.now()
    monthly_postcards = sum(
        1 for p in postcards
        if p.created_at.year == now.year and p.created_at.month == now.month
    )

    # Get member since (user's date joined)
    member_since = request.user.date_joined

    # Get latest postcard
    latest_postcard = max(postcards, key=lambda p: p.created_at) if postcards else None

    # Get recent postcards (latest 5) for display
    sorted_by_date = sorted(postcards, key=lambda p: p.created_at, reverse=True)
    recent_postcards = sorted_by_date[:5]

    # Add first line for display in card (first line only)
    for postcard in recent_postcards:
        if postcard.haiku:
            # Split by newline and take the first line
            postcard.haiku_first_line = postcard.haiku.split('\n')[0]
        else:
            postcard.haiku_first_line = ''

    # Mood counts
    expected_moods = ['Happy', 'Calm', 'Neutral', 'Stressed', 'Tired']
    mood_counts = {mood: 0 for mood in expected_moods}
    for postcard in postcards:
        mood = postcard.mood
        if mood in mood_counts:
            mood_counts[mood] += 1
    if total_postcards > 0:
        most_common_mood = max(mood_counts, key=mood_counts.get)
    else:
        most_common_mood = "No data"

    # Total words written
    total_words = sum(len(p.reflection.split()) for p in postcards)

    # Streak calculation
    if postcards:
        # Get distinct dates (date only) of postcards
        dates = {p.created_at.date() for p in postcards}
        dates_list = sorted(dates)
        # longest streak calculation
        longest = 0
        current_len = 0
        prev_date = None
        for d in dates_list:
            if prev_date is None or (d - prev_date).days == 1:
                current_len += 1
            else:
                if current_len > longest:
                    longest = current_len
                current_len = 1
            prev_date = d
        if current_len > longest:
            longest = current_len
        longest_streak = longest

        # Current stripe (including today if applicable)
        today = timezone.now().date()
        if today in dates:
            current_streak = 1
            y = today - timedelta(days=1)
            while y in dates:
                current_streak += 1
                y -= timedelta(days=1)
        else:
            current_streak = 0
    else:
        longest_streak = 0
        current_streak = 0

    # Monthly chart data (last six months)
    # Build a dictionary of counts by (year, month)
    monthly_counts_dict = defaultdict(int)
    for p in postcards:
        key = (p.created_at.year, p.created_at.month)
        monthly_counts_dict[key] += 1

    # Generate the last six months (including current) in chronological order (oldest first)
    month_labels = []
    month_counts = []
    current_year = now.year
    current_month = now.month
    months_list = []
    for i in range(5, -1, -1):  # i from 5 down to 0
        month = current_month - i
        year = current_year
        while month < 1:
            month += 12
            year -= 1
        months_list.append((year, month))

    # Now months_list is from oldest to most recent? Let's see: i=5 -> 5 months ago, i=0 -> current month.
    # So the list is [current-5, current-4, ..., current-1, current] -> already chronological (oldest first).
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for year, month in months_list:
        label = f"{month_names[month-1]} {year}"
        month_labels.append(label)
        month_counts.append(monthly_counts_dict.get((year, month), 0))

    context = {
        'total_postcards': total_postcards,
        'monthly_postcards': monthly_postcards,
        'member_since': member_since,
        'latest_postcard': latest_postcard,
        'postcards': recent_postcards,  # For backward compatibility with template
        'most_common_mood': most_common_mood,
        'total_words': total_words,
        'current_streak': current_streak,
        'longest_streak': longest_streak,
        'mood_counts': mood_counts,
        # Chart.js data
        'mood_labels': list(mood_counts.keys()),
        'mood_values': list(mood_counts.values()),
        'month_labels': month_labels,
        'month_counts': month_counts,
    }

    return render(request, 'dashboard.html', context)


@login_required
def postcard_history(request):
    # Get all postcards for the current user
    postcards = Postcard.objects.filter(user=request.user)
    query = request.GET.get('q')
    mood_filter = request.GET.get('mood')
    sort = request.GET.get('sort')

    # Apply search
    if query:
        postcards = postcards.filter(
            Q(goal__icontains=query) |
            Q(reflection__icontains=query) |
            Q(haiku__icontains=query)
        )

    # Apply mood filter (if not 'All')
    if mood_filter and mood_filter != 'All':
        postcards = postcards.filter(mood=mood_filter)

    # Apply sorting
    if sort == 'oldest':
        postcards = postcards.order_by('created_at')
    else:  # default to newest
        postcards = postcards.order_by('-created_at')

    # Add first line for display in card (first line only)
    for postcard in postcards:
        if postcard.haiku:
            # Split by newline and take the first line
            postcard.haiku_first_line = postcard.haiku.split('\n')[0]
        else:
            postcard.haiku_first_line = ''

    context = {
        'postcards': postcards,
        'query': query,
        'mood_filter': mood_filter,
        'sort': sort,
    }

    return render(request, 'postcard_history.html', context)


@login_required
def history(request):
    # Get all digital summaries for the current user
    summaries = DigitalSummary.objects.filter(user=request.user)
    sort = request.GET.get('sort')

    # Apply sorting
    if sort == 'oldest':
        summaries = summaries.order_by('created_at')
    else:  # default to newest
        summaries = summaries.order_by('-created_at')

    # Calculate the average wellness score
    wellness_sum = summaries.aggregate(total=Sum('wellness_score'))['total'] or 0
    count = summaries.count()
    if count > 0:
        avg_wellness = wellness_sum / count
    else:
        avg_wellness = 0.0

    context = {
        'summaries': summaries,
        'sort': sort,
        'avg_wellness': avg_wellness,
    }

    return render(request, 'history.html', context)


@login_required
def delete_postcard(request, postcard_id):
    postcard = get_object_or_404(Postcard, id=postcard_id, user=request.user)
    if request.method == "POST":
        postcard.delete()
        messages.success(request, "Postcard deleted successfully.")
        return redirect('core:history')
    # If GET, show a confirmation page (or just redirect back)
    return redirect('core:history')


@login_required
def view_postcard(request, postcard_id):
    postcard = get_object_or_404(Postcard, id=postcard_id, user=request.user)
    # Prepare the postcard data in the same format as the home view uses for the result template
    postcard_data = {
        'mood': postcard.mood,
        'goal': postcard.goal,
        'screen_time': postcard.screen_time,
        'has_report': postcard.has_report,
        'filename': postcard.filename,
        'haiku': postcard.haiku,
        'reflection': postcard.reflection,
        'action': postcard.action,
        'pledge': postcard.pledge,
    }
    return render(request, 'result.html', postcard_data)


@login_required
def download_postcard_by_id(request, postcard_id, file_format):
    # Get the postcard for the current user
    postcard = get_object_or_404(Postcard, id=postcard_id, user=request.user)

    # Prepare postcard data in the same format as the session expects
    postcard_data = {
        'mood': postcard.mood,
        'goal': postcard.goal,
        'screen_time': postcard.screen_time,
        'has_report': postcard.has_report,
        'filename': postcard.filename,
        'haiku': postcard.haiku,
        'reflection': postcard.reflection,
        'action': postcard.action,
        'pledge': postcard.pledge,
    }

    if file_format == "png":
        content = render_postcard_png(postcard_data)
        content_type = "image/png"
    elif file_format == "pdf":
        content = render_postcard_pdf(postcard_data)
        content_type = "application/pdf"
    else:
        raise Http404("Unsupported postcard format.")

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="unscroll-postcard-{postcard.id}.{file_format}"'
    return response


# Password reset views using Django's built-in views (we'll just point to them in urls.py)
# But we can also implement custom ones if needed. For simplicity, we'll use the built-in ones.
# However, we need to create the templates for them.
# We'll leave the implementation of the actual sending of emails to the built-in views.
# We just need to map the URLs in urls.py.