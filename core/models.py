from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from django.utils import timezone
import uuid


class UserProfile(models.Model):
    MOMENTUM_PERIOD_CHOICES = [
        ("all", "All time"),
        ("week", "This week"),
        ("month", "This month"),
        ("30days", "Last 30 days"),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    bio = models.TextField(max_length=500, blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    newsletter_subscribe = models.BooleanField(default=False)
    default_momentum_period = models.CharField(
        max_length=10,
        choices=MOMENTUM_PERIOD_CHOICES,
        default="all",
    )
    show_skipped_rescue_statistics = models.BooleanField(default=True)
    show_detailed_mobile_analytics = models.BooleanField(default=True)
    show_interaction_metrics = models.BooleanField(default=True)
    show_actionable_inputs = models.BooleanField(default=True)
    preferred_daily_screen_time_minutes = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
    )
    timezone = models.CharField(max_length=64, default="UTC")
    reminders_enabled = models.BooleanField(default=False)
    email_reminders = models.BooleanField(default=False)
    in_app_reminders = models.BooleanField(default=True)
    device_reminders = models.BooleanField(default=False)
    weekly_review_reminder = models.BooleanField(default=False)
    target_reminder = models.BooleanField(default=False)
    goal_reminders = models.BooleanField(default=False)
    stale_device_reminder = models.BooleanField(default=False)
    quiet_hours_start = models.TimeField(blank=True, null=True)
    quiet_hours_end = models.TimeField(blank=True, null=True)
    preferred_reminder_time = models.TimeField(blank=True, null=True)
    preferred_reminder_days = models.JSONField(default=list, blank=True)
    email_verified_at = models.DateTimeField(blank=True, null=True)
    pending_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.user.username


class Postcard(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='postcards')
    mood = models.CharField(max_length=20)
    goal = models.CharField(max_length=20)
    screen_time = models.CharField(max_length=20, blank=True, null=True)
    has_report = models.BooleanField(default=False)
    filename = models.CharField(max_length=255, blank=True, null=True)
    haiku = models.TextField()
    reflection = models.TextField()
    action = models.TextField()
    pledge = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=["user", "-created_at"], name="postcard_user_created_idx"),
        ]

    def __str__(self):
        return f"{self.user.username}'s postcard from {self.created_at.strftime('%Y-%m-%d')}"


class DigitalSummary(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='digital_summaries')
    created_at = models.DateTimeField(auto_now_add=True)
    screen_time_minutes = models.IntegerField()
    wellness_score = models.IntegerField()
    category = models.CharField(max_length=20)
    insight = models.TextField()
    # NULL identifies summaries created before historical Goal Rescue
    # snapshots existed. An empty/no-rescue result is stored as a real
    # dictionary so it cannot later acquire a recommendation.
    goal_rescue_snapshot = models.JSONField(blank=True, null=True)
    app_usage = models.JSONField(default=list, blank=True)
    # Immutable, structured extraction and interpretation captured when the
    # report is created. Legacy summaries intentionally retain empty objects.
    mobile_analytics_snapshot = models.JSONField(default=dict, blank=True)
    mobile_assessment_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=["user", "-created_at"], name="summary_user_created_idx"),
        ]


    def __str__(self):
        return f"{self.user.username} summary on {self.created_at.strftime('%Y-%m-%d %H:%M')}"


class UserGoal(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_PAUSED = "paused"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_COMPLETED, "Completed"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="goals",
    )
    title = models.CharField(max_length=160)
    why_it_matters = models.TextField(max_length=800)
    current_focus = models.CharField(max_length=200, blank=True)
    progress_unit = models.CharField(max_length=80)
    weekly_target = models.DecimalField(max_digits=8, decimal_places=2)
    is_primary = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    preferred_days = models.JSONField(default=list, blank=True)
    preferred_time = models.TimeField(blank=True, null=True)
    deadline = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(
                    is_primary=True,
                    status="active",
                ),
                name="one_active_primary_goal_per_user",
            ),
            models.CheckConstraint(
                condition=models.Q(weekly_target__gt=0),
                name="goal_weekly_target_gt_zero",
            ),
        ]

    def clean(self):
        super().clean()

        if not self.user_id or self.status != self.STATUS_ACTIVE:
            return

        other_active_goals = UserGoal.objects.filter(
            user_id=self.user_id,
            status=self.STATUS_ACTIVE,
        ).exclude(pk=self.pk)

        if other_active_goals.count() >= 3:
            raise ValidationError(
                "A user can have at most three active goals."
            )

        if self.is_primary and other_active_goals.filter(
            is_primary=True
        ).exists():
            raise ValidationError(
                "A user can have only one active primary goal."
            )

    def __str__(self):
        return f"{self.user.username}: {self.title}"


class GoalAction(models.Model):
    SIZE_MINIMUM = "minimum"
    SIZE_STANDARD = "standard"
    SIZE_DEEP = "deep"

    SIZE_CHOICES = [
        (SIZE_MINIMUM, "Minimum"),
        (SIZE_STANDARD, "Standard"),
        (SIZE_DEEP, "Deep"),
    ]

    goal = models.ForeignKey(
        UserGoal,
        on_delete=models.CASCADE,
        related_name="actions",
    )
    size = models.CharField(max_length=20, choices=SIZE_CHOICES)
    title = models.CharField(max_length=200)
    duration_minutes = models.PositiveSmallIntegerField()
    progress_value = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=1,
    )

    class Meta:
        ordering = ["duration_minutes"]
        constraints = [
            models.UniqueConstraint(
                fields=["goal", "size"],
                name="one_action_size_per_goal",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name="goal_action_duration_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(progress_value__gt=0),
                name="goal_action_progress_gt_zero",
            ),
        ]

    def __str__(self):
        return f"{self.goal.title} — {self.get_size_display()}"


class MomentumEntry(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="momentum_entries",
    )
    goal = models.ForeignKey(
        UserGoal,
        on_delete=models.SET_NULL,
        related_name="momentum_entries",
        blank=True,
        null=True,
    )
    action = models.ForeignKey(
        GoalAction,
        on_delete=models.SET_NULL,
        related_name="momentum_entries",
        blank=True,
        null=True,
    )
    digital_summary = models.OneToOneField(
        DigitalSummary,
        on_delete=models.CASCADE,
        related_name="momentum_entry",
    )
    action_title = models.CharField(max_length=200)
    action_size = models.CharField(
        max_length=20,
        choices=GoalAction.SIZE_CHOICES,
    )
    duration_minutes = models.PositiveSmallIntegerField()
    progress_value = models.DecimalField(
        max_digits=8,
        decimal_places=2,
    )
    progress_unit = models.CharField(max_length=80)
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-completed_at"]
        indexes = [
            models.Index(fields=["user", "-completed_at"], name="momentum_user_done_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name="momentum_duration_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(progress_value__gt=0),
                name="momentum_progress_gt_zero",
            ),
        ]

    def __str__(self):
        return (
            f"{self.user.username}: {self.action_title} "
            f"on {self.completed_at:%Y-%m-%d}"
        )


class GoalRescueOutcome(models.Model):
    STATUS_SHOWN = "shown"
    STATUS_COMPLETED = "completed"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = [
        (STATUS_SHOWN, "Shown"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="goal_rescue_outcomes",
    )
    digital_summary = models.OneToOneField(
        DigitalSummary,
        on_delete=models.CASCADE,
        related_name="goal_rescue_outcome",
    )
    goal = models.ForeignKey(
        UserGoal,
        on_delete=models.SET_NULL,
        related_name="goal_rescue_outcomes",
        blank=True,
        null=True,
    )
    action = models.ForeignKey(
        GoalAction,
        on_delete=models.SET_NULL,
        related_name="goal_rescue_outcomes",
        blank=True,
        null=True,
    )
    action_size = models.CharField(max_length=20, choices=GoalAction.SIZE_CHOICES)
    action_title = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_SHOWN,
    )
    shown_at = models.DateTimeField()
    completed_at = models.DateTimeField(blank=True, null=True)
    skipped_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-shown_at"]
        indexes = [
            models.Index(fields=["user", "-shown_at"], name="rescue_user_shown_idx"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.action_title} ({self.status})"


class ActionableInputFeedback(models.Model):
    OUTCOME_HELPFUL = "helpful"
    OUTCOME_USED = "used"
    OUTCOME_NOT_USEFUL = "not_useful"
    OUTCOME_CHOICES = [
        (OUTCOME_HELPFUL, "Helpful"),
        (OUTCOME_USED, "Used this input"),
        (OUTCOME_NOT_USEFUL, "Not useful"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="actionable_input_feedback",
    )
    digital_summary = models.ForeignKey(
        DigitalSummary,
        on_delete=models.CASCADE,
        related_name="actionable_input_feedback",
    )
    input_id = models.CharField(max_length=80)
    input_type = models.CharField(max_length=40)
    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "digital_summary", "input_id"],
                name="one_feedback_per_actionable_input",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "input_type", "outcome"], name="input_feedback_user_idx"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.input_type} ({self.outcome})"


APP_CATEGORY_CHOICES = [
    (value, value) for value in (
        "Social", "Entertainment", "Education", "Productivity", "Communication",
        "Games", "Browser", "Utilities", "Health/Fitness", "Finance", "Work",
        "Other", "Unknown",
    )
]


class UserAppPreference(models.Model):
    PURPOSE_CHOICES = [(value, value) for value in (
        "Goal aligned", "Useful", "Neutral", "Distracting", "Mixed", "Unknown",
    )]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="app_preferences")
    normalized_app_name = models.CharField(max_length=160)
    display_name = models.CharField(max_length=160)
    category = models.CharField(max_length=32, choices=APP_CATEGORY_CHOICES, default="Unknown")
    purpose = models.CharField(max_length=24, choices=PURPOSE_CHOICES, default="Unknown")
    linked_goal = models.ForeignKey(UserGoal, on_delete=models.SET_NULL, null=True, blank=True, related_name="app_preferences")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]
        constraints = [models.UniqueConstraint(fields=["user", "normalized_app_name"], name="unique_user_app_preference")]
        indexes = [models.Index(fields=["user", "normalized_app_name"], name="app_pref_user_name_idx")]


class ScreenTimeTarget(models.Model):
    TYPE_OVERALL = "overall"
    TYPE_APP = "app"
    TYPE_CATEGORY = "category"
    TYPE_CHOICES = [(TYPE_OVERALL, "Overall"), (TYPE_APP, "App"), (TYPE_CATEGORY, "Category")]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="screen_time_targets")
    target_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    key = models.CharField(max_length=160, blank=True)
    daily_minutes = models.PositiveSmallIntegerField()
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "target_type", "key"], name="unique_user_screen_target"),
            models.CheckConstraint(condition=models.Q(daily_minutes__gt=0), name="screen_target_minutes_gt_zero"),
        ]
        indexes = [models.Index(fields=["user", "target_type", "enabled"], name="target_user_type_idx")]


class Reminder(models.Model):
    TYPE_CHOICES = [(value, label) for value, label in (
        ("goal", "Goal action"), ("weekly", "Weekly Review"), ("deadline", "Goal deadline"),
        ("resume", "Resume paused goal"), ("target", "Screen-time target"), ("device", "Device sync stale"),
    )]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reminders")
    reminder_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    title = models.CharField(max_length=160)
    message = models.CharField(max_length=400)
    link = models.CharField(max_length=240, blank=True)
    due_at = models.DateTimeField()
    enabled = models.BooleanField(default=True)
    last_dispatched_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["enabled", "due_at"], name="reminder_due_idx")]


class InAppNotification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    notification_type = models.CharField(max_length=24)
    title = models.CharField(max_length=160)
    message = models.CharField(max_length=400)
    link = models.CharField(max_length=240, blank=True)
    read_at = models.DateTimeField(blank=True, null=True)
    dedupe_key = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "dedupe_key"], condition=~models.Q(dedupe_key=""), name="unique_notification_dedupe")]
        indexes = [models.Index(fields=["user", "read_at", "-created_at"], name="notification_user_idx")]


class EmailVerification(models.Model):
    PURPOSE_VERIFY = "verify"
    PURPOSE_CHANGE = "change"
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_verifications")
    purpose = models.CharField(max_length=12, choices=[(PURPOSE_VERIFY, "Verify"), (PURPOSE_CHANGE, "Change")])
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SecurityEvent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="security_events")
    event_type = models.CharField(max_length=40)
    occurred_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["user", "-occurred_at"], name="security_user_event_idx")]


class UserDevice(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=120)
    platform = models.CharField(max_length=16, choices=[("android", "Android"), ("ios", "iOS")])
    app_version = models.CharField(max_length=32, blank=True)
    device_model = models.CharField(max_length=80, blank=True)
    os_version = models.CharField(max_length=32, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    token_rotated_at = models.DateTimeField(default=timezone.now)
    consent_version = models.CharField(max_length=16)
    consent_accepted_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(blank=True, null=True)
    last_sync_attempt_at = models.DateTimeField(blank=True, null=True)
    last_successful_sync_at = models.DateTimeField(blank=True, null=True)
    last_sync_status = models.CharField(max_length=24, blank=True)
    last_sync_error_code = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [models.Index(fields=["user", "is_active", "-last_successful_sync_at"], name="device_user_status_idx")]


class DevicePairingCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="pairing_codes")
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(blank=True, null=True)
    consent_version = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["expires_at", "consumed_at"], name="pairing_expiry_idx")]


class DeviceAnalyticsReport(models.Model):
    device = models.ForeignKey(UserDevice, on_delete=models.CASCADE, related_name="reports")
    summary = models.OneToOneField(DigitalSummary, on_delete=models.CASCADE, related_name="device_report")
    device_report_id = models.CharField(max_length=120)
    schema_version = models.PositiveSmallIntegerField(default=1)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["device", "device_report_id"], name="unique_device_report_id")]
        indexes = [models.Index(fields=["device", "-received_at"], name="device_report_received_idx")]


class MaintenanceJobRun(models.Model):
    job_name = models.CharField(max_length=80, unique=True)
    last_run_at = models.DateTimeField()
    last_success_at = models.DateTimeField(blank=True, null=True)
    last_failure_at = models.DateTimeField(blank=True, null=True)
    processed_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16)
    error_code = models.CharField(max_length=40, blank=True)

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.userprofile.save()
