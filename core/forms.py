from pathlib import Path

from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User

from .models import UserProfile

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