from pathlib import Path

from django import forms


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
