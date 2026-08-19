from django.contrib import admin

from .models import (
    DigitalSummary,
    GoalAction,
    GoalRescueOutcome,
    MomentumEntry,
    Postcard,
    UserGoal,
    UserProfile,
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
    list_display = ("user", "newsletter_subscribe", "created_at", "updated_at")
    list_filter = ("newsletter_subscribe",)
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DigitalSummary)
class DigitalSummaryAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "screen_time_minutes", "wellness_score", "category")
    list_filter = ("category", "created_at")
    search_fields = ("user__username", "user__email", "insight")
    readonly_fields = ("created_at", "goal_rescue_snapshot")
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
