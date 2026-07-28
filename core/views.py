from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from django.http import HttpResponse, Http404
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import os
from django.conf import settings
from .models import Postcard, DigitalSummary, UserProfile
from .forms import PostcardForm, SignUpForm, UserLoginForm, UserProfileForm
from .services import (
    format_screen_time,
    generate_postcard,
    render_postcard_pdf,
    render_postcard_png,
)
from services.screen_time_parser import parse_screen_time_report


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
        messages.info(request, "No summary data available. Please generate a postcard first.")
        return redirect("core:home")

    return render(request, "summary.html", {"summary": summary_data})


def download_postcard(request, file_format):
    postcard_data = request.session.get("postcard")
    if not postcard_data:
        messages.error(request, "No postcard data found. Please generate a postcard first.")
        return redirect("core:home")

    if file_format == "pdf":
        content = render_postcard_pdf(postcard_data)
        content_type = "application/pdf"
        filename = "postcard.pdf"
    elif file_format == "png":
        content = render_postcard_png(postcard_data)
        content_type = "image/png"
        filename = "postcard.png"
    else:
        messages.error(request, "Invalid format specified.")
        return redirect("core:summary")

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def dashboard(request):
    # Get user's recent postcards and summaries
    recent_postcards = Postcard.objects.filter(user=request.user)[:5]
    recent_summaries = DigitalSummary.objects.filter(user=request.user)[:5]

    context = {
        "recent_postcards": recent_postcards,
        "recent_summaries": recent_summaries,
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


def history(request):
    summaries = DigitalSummary.objects.all().order_by('-created_at')
    return render(request, "history.html", {"summaries": summaries})


def postcard_history(request):
    postcards = Postcard.objects.all().order_by('-created_at')
    return render(request, "postcard_history.html", {"postcards": postcards})


def view_summary(request, summary_id):
    summary = get_object_or_404(DigitalSummary, id=summary_id)
    return render(request, "view_summary.html", {"summary": summary})


def download_postcard_by_id(request, postcard_id, file_format):
    postcard = get_object_or_404(Postcard, id=postcard_id)

    if file_format == "pdf":
        content = render_postcard_pdf({
            "mood": postcard.mood,
            "goal": postcard.goal,
            "screen_time": postcard.screen_time or "0m",
            "has_report": postcard.has_report,
            "filename": postcard.filename,
        })
        content_type = "application/pdf"
        filename = f"postcard_{postcard_id}.pdf"
    elif file_format == "png":
        content = render_postcard_png({
            "mood": postcard.mood,
            "goal": postcard.goal,
            "screen_time": postcard.screen_time or "0m",
            "has_report": postcard.has_report,
            "filename": postcard.filename,
        })
        content_type = "image/png"
        filename = f"postcard_{postcard_id}.png"
    else:
        raise Http404("Format not supported")

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response