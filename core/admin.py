from django.contrib import admin

from .models import (
    DigitalSummary,
    GoalAction,
    GoalRescueOutcome,
    MomentumEntry,
    Postcard,
    UserGoal,
    UserProfile,
    ActionableInputFeedback,
    DeviceAnalyticsReport, DevicePairingCode, EmailVerification, InAppNotification,
    MaintenanceJobRun, Reminder, ScreenTimeTarget, SecurityEvent, UserAppPreference,
    UserDevice,
)


class GoalActionInline(admin.TabularInline):
    model = GoalAction
    extra = 0


@admin.register(UserGoal)
class UserGoalAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "user",
        "is_primary",
        "status",
        "weekly_target",
        "progress_unit",
        "updated_at",
    )
    list_filter = ("status", "is_primary")
    search_fields = ("title", "user__username")
    inlines = [GoalActionInline]


@admin.register(GoalAction)
class GoalActionAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "goal",
        "size",
        "duration_minutes",
        "progress_value",
    )
    list_filter = ("size",)
    search_fields = ("title", "goal__title")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "newsletter_subscribe", "show_detailed_mobile_analytics", "created_at", "updated_at")
    list_filter = ("newsletter_subscribe", "show_detailed_mobile_analytics", "show_actionable_inputs")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DigitalSummary)
class DigitalSummaryAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "screen_time_minutes", "ingestion_source", "total_basis", "wellness_score", "category")
    list_filter = ("category", "ingestion_source", "total_basis", "was_user_confirmed", "created_at")
    search_fields = ("user__username", "user__email", "insight")
    readonly_fields = ("created_at", "goal_rescue_snapshot", "mobile_analytics_snapshot", "mobile_assessment_snapshot")
    date_hierarchy = "created_at"


@admin.register(Postcard)
class PostcardAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "mood", "goal", "has_report")
    list_filter = ("mood", "goal", "has_report", "created_at")
    search_fields = ("user__username", "haiku", "reflection")
    readonly_fields = ("created_at",)


@admin.register(MomentumEntry)
class MomentumEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "action_title", "goal", "action_size", "completed_at")
    list_filter = ("action_size", "completed_at")
    search_fields = ("user__username", "action_title", "goal__title")
    readonly_fields = ("completed_at",)
    date_hierarchy = "completed_at"


@admin.register(GoalRescueOutcome)
class GoalRescueOutcomeAdmin(admin.ModelAdmin):
    list_display = ("user", "action_title", "goal", "status", "shown_at")
    list_filter = ("status", "action_size", "shown_at")
    search_fields = ("user__username", "action_title", "goal__title")
    readonly_fields = ("shown_at", "completed_at", "skipped_at")
    date_hierarchy = "shown_at"


@admin.register(ActionableInputFeedback)
class ActionableInputFeedbackAdmin(admin.ModelAdmin):
    list_display = ("user", "input_type", "outcome", "digital_summary", "created_at")
    list_filter = ("input_type", "outcome", "created_at")
    search_fields = ("user__username", "input_id", "input_type")
    readonly_fields = ("user", "digital_summary", "input_id", "input_type", "outcome", "created_at")

for model in (UserAppPreference, ScreenTimeTarget, Reminder, InAppNotification, EmailVerification, SecurityEvent, UserDevice, DevicePairingCode, DeviceAnalyticsReport, MaintenanceJobRun):
    admin.site.register(model)
