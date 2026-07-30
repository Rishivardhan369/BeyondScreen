from pathlib import Path
from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User

from .models import UserGoal, UserProfile

ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".jpg", ".jpeg", ".pdf", ".png", ".txt"}

class PostcardForm(forms.Form):
    """Collect the small amount of context needed to make a postcard."""

    file = forms.FileField(
        required=False,
        label="Screen Time report (optional)",
        help_text="A screenshot, PDF, CSV, or text export is welcome.",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.pdf,.png,.jpg,.jpeg,.txt"}),
    )
    screen_time = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=1440,
        label="Screen time today (minutes, optional)",
        widget=forms.NumberInput(
            attrs={"placeholder": "e.g. 245", "inputmode": "numeric", "min": 0, "max": 1440}
        ),
    )
    mood = forms.ChoiceField(
        choices=[
            ("", "Choose your mood"),
            ("Happy", "Happy"),
            ("Calm", "Calm"),
            ("Neutral", "Neutral"),
            ("Stressed", "Stressed"),
            ("Tired", "Tired"),
        ],
        label="How are you feeling?",
    )
    goal = forms.ChoiceField(
        choices=[
            ("", "Choose tomorrow's intention"),
            ("Study", "Study"),
            ("Fitness", "Fitness"),
            ("Better Sleep", "Better sleep"),
            ("Productivity", "Productivity"),
            ("Presence", "Be more present"),
        ],
        label="Tomorrow's intention",
    )

    def clean_file(self):
        uploaded_file = self.cleaned_data.get("file")
        if not uploaded_file:
            return uploaded_file

        if uploaded_file.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Please choose a file smaller than 10 MB.")

        if Path(uploaded_file.name).suffix.lower() not in ALLOWED_UPLOAD_EXTENSIONS:
            raise forms.ValidationError("Choose a CSV, PDF, PNG, JPG, or text file.")

        return uploaded_file


DAY_CHOICES = [
    ("monday", "Monday"),
    ("tuesday", "Tuesday"),
    ("wednesday", "Wednesday"),
    ("thursday", "Thursday"),
    ("friday", "Friday"),
    ("saturday", "Saturday"),
    ("sunday", "Sunday"),
]


class GoalDNAForm(forms.ModelForm):
    PROGRESS_UNIT_CHOICES = [
        ("", "Choose how you want to measure progress"),
        ("minutes", "Minutes spent"),
        ("sessions", "Sessions completed"),
        ("tasks", "Tasks completed"),
        ("questions", "Questions solved"),
        ("pages", "Pages completed"),
        ("workouts", "Workouts completed"),
        ("custom", "Custom measurement"),
    ]

    progress_unit = forms.ChoiceField(
        choices=PROGRESS_UNIT_CHOICES,
        label="How would you like to measure progress?",
    )
    custom_progress_unit = forms.CharField(
        max_length=80,
        required=False,
        label="Name your measurement",
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. chapters completed",
            }
        ),
    )
    weekly_target = forms.IntegerField(
        min_value=1,
        max_value=100000,
        label="Your weekly aim",
        widget=forms.NumberInput(
            attrs={
                "placeholder": "e.g. 5",
                "step": "1",
                "min": "1",
            }
        ),
    )
    preferred_days = forms.MultipleChoiceField(
        choices=DAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Which days usually work best?",
    )
    preferred_time = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}),
        label="What time usually works best?",
    )
    deadline = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Do you have a deadline?",
    )

    minimum_action_title = forms.CharField(
        max_length=200,
        label="What will you do?",
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. Solve one easy coding problem",
            }
        ),
    )
    minimum_action_minutes = forms.IntegerField(
        min_value=1,
        max_value=1440,
        initial=10,
        label="How long will it take?",
    )
    minimum_action_progress = forms.IntegerField(
        min_value=1,
        max_value=100000,
        required=False,
        label="What will you complete?",
        widget=forms.NumberInput(
            attrs={
                "placeholder": "Enter a whole number",
                "step": "1",
                "min": "1",
            }
        ),
    )

    standard_action_title = forms.CharField(
        max_length=200,
        label="What will you do?",
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. Solve three coding problems",
            }
        ),
    )
    standard_action_minutes = forms.IntegerField(
        min_value=1,
        max_value=1440,
        initial=25,
        label="How long will it take?",
    )
    standard_action_progress = forms.IntegerField(
        min_value=1,
        max_value=100000,
        required=False,
        label="What will you complete?",
        widget=forms.NumberInput(
            attrs={
                "placeholder": "Enter a whole number",
                "step": "1",
                "min": "1",
            }
        ),
    )

    deep_action_title = forms.CharField(
        max_length=200,
        label="What will you do?",
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. Complete a full practice set",
            }
        ),
    )
    deep_action_minutes = forms.IntegerField(
        min_value=1,
        max_value=1440,
        initial=60,
        label="How long will it take?",
    )
    deep_action_progress = forms.IntegerField(
        min_value=1,
        max_value=100000,
        required=False,
        label="What will you complete?",
        widget=forms.NumberInput(
            attrs={
                "placeholder": "Enter a whole number",
                "step": "1",
                "min": "1",
            }
        ),
    )

    class Meta:
        model = UserGoal
        fields = (
            "title",
            "why_it_matters",
            "current_focus",
            "progress_unit",
            "weekly_target",
            "preferred_days",
            "preferred_time",
            "deadline",
        )
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "placeholder": "e.g. Become confident at public speaking",
                }
            ),
            "why_it_matters": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": (
                        "e.g. I want to perform better in interviews "
                        "and express my ideas clearly."
                    ),
                }
            ),
            "current_focus": forms.TextInput(
                attrs={
                    "placeholder": (
                        "Optional: e.g. Speaking without reading from notes"
                    )
                }
            ),
        }

    def clean_title(self):
        title = self.cleaned_data["title"].strip()

        if len(title) < 3:
            raise forms.ValidationError(
                "Describe your goal in at least three characters."
            )

        return title

    def clean_why_it_matters(self):
        reason = self.cleaned_data["why_it_matters"].strip()

        if len(reason) < 5:
            raise forms.ValidationError(
                "Add a short reason explaining why this goal matters."
            )

        return reason

    def clean_deadline(self):
        deadline = self.cleaned_data.get("deadline")

        if deadline and deadline < date.today():
            raise forms.ValidationError(
                "Choose today or a future date."
            )

        return deadline

    def clean(self):
        cleaned_data = super().clean()

        selected_unit = cleaned_data.get("progress_unit")
        custom_unit = (
            cleaned_data.get("custom_progress_unit") or ""
        ).strip()

        if selected_unit == "custom":
            if len(custom_unit) < 2:
                self.add_error(
                    "custom_progress_unit",
                    "Tell us what you want to count.",
                )
            else:
                cleaned_data["progress_unit"] = custom_unit.lower()
                cleaned_data["progress_unit_label"] = custom_unit
        else:
            labels = dict(self.PROGRESS_UNIT_CHOICES)
            cleaned_data["progress_unit_label"] = labels.get(
                selected_unit,
                "",
            )

        minimum_minutes = cleaned_data.get(
            "minimum_action_minutes"
        )
        standard_minutes = cleaned_data.get(
            "standard_action_minutes"
        )
        deep_minutes = cleaned_data.get(
            "deep_action_minutes"
        )

        if (
            minimum_minutes is not None
            and standard_minutes is not None
            and deep_minutes is not None
            and not minimum_minutes <= standard_minutes <= deep_minutes
        ):
            raise forms.ValidationError(
                "The time should increase from the small step "
                "to the regular step and then to the bigger step."
            )

        action_sizes = ("minimum", "standard", "deep")
        final_unit = cleaned_data.get("progress_unit")

        for size in action_sizes:
            minutes = cleaned_data.get(
                f"{size}_action_minutes"
            )

            if final_unit == "minutes":
                cleaned_data[f"{size}_action_progress"] = minutes
            elif final_unit == "sessions":
                cleaned_data[f"{size}_action_progress"] = 1
            else:
                progress_field = f"{size}_action_progress"
                progress_value = cleaned_data.get(progress_field)

                if progress_value is None:
                    self.add_error(
                        progress_field,
                        "Enter what this step will complete.",
                    )

        return cleaned_data

    def to_session_data(self):
        data = self.cleaned_data

        return {
            "title": data["title"],
            "why_it_matters": data["why_it_matters"],
            "current_focus": data.get("current_focus", ""),
            "progress_unit": data["progress_unit"],
            "progress_unit_label": data.get(
                "progress_unit_label",
                data["progress_unit"],
            ),
            "weekly_target": str(data["weekly_target"]),
            "preferred_days": list(
                data.get("preferred_days", [])
            ),
            "preferred_time": (
                data["preferred_time"].isoformat()
                if data.get("preferred_time")
                else None
            ),
            "deadline": (
                data["deadline"].isoformat()
                if data.get("deadline")
                else None
            ),
            "actions": {
                "minimum": {
                    "title": data["minimum_action_title"],
                    "duration_minutes": data[
                        "minimum_action_minutes"
                    ],
                    "progress_value": str(
                        data["minimum_action_progress"]
                    ),
                },
                "standard": {
                    "title": data["standard_action_title"],
                    "duration_minutes": data[
                        "standard_action_minutes"
                    ],
                    "progress_value": str(
                        data["standard_action_progress"]
                    ),
                },
                "deep": {
                    "title": data["deep_action_title"],
                    "duration_minutes": data[
                        "deep_action_minutes"
                    ],
                    "progress_value": str(
                        data["deep_action_progress"]
                    ),
                },
            },
        }


class SignUpForm(UserCreationForm):
    email = forms.EmailField(
        max_length=254,
        help_text="Required. Inform a valid email address.",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    first_name = forms.CharField(
        max_length=30,
        required=False,
        help_text="Optional.",
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        max_length=30,
        required=False,
        help_text="Optional.",
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "password1", "password2")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        if commit:
            user.save()
            # Create a user profile for the new user
            UserProfile.objects.get_or_create(user=user)
        return user


class UserLoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Username or Email",
        max_length=254,
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("bio", "avatar", "newsletter_subscribe")
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 4, "placeholder": "Tell us about yourself..."}),
            "avatar": forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png"}),
        }
