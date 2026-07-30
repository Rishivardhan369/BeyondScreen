from django.contrib import admin

from .models import GoalAction, UserGoal


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
