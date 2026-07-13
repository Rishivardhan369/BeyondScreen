"""Content generation and export helpers for digital postcards."""

from io import BytesIO
from itertools import count
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas

_generation_counter = count()

MOOD_OPENERS = {
    "Happy": "Your good energy is worth protecting and directing with care.",
    "Calm": "This steadier moment can become a foundation for a thoughtful tomorrow.",
    "Neutral": "A quiet reset can begin with one deliberate choice.",
    "Stressed": "You deserve a pause that makes the next step feel lighter.",
    "Tired": "Gentle boundaries matter most when your energy is low.",
}
GOAL_LINES = {
    "Study": "Make room for the kind of focus that lets one meaningful page become progress.",
    "Fitness": "Let your next movement be an act of care, not another notification cue.",
    "Better Sleep": "A calmer evening begins by giving your mind fewer bright signals to follow.",
    "Productivity": "Protect the work that matters before giving attention away.",
    "Presence": "Choose one real-world moment to notice before reaching for a screen.",
}
REPORT_LINES = {
    True: "Your uploaded report is a useful snapshot, not a scorecard.",
    False: "You do not need perfect data to make an intentional next choice.",
}

HAIKU_FIRST_LINES = (
    "Blue light fades softly", "A quiet screen rests", "Morning waits outside", "One bright window sleeps",
    "Notifications hush", "A still pocket glows", "Evening clears slowly", "The room breathes again",
    "Small moments return", "Your attention lands",
)
HAIKU_SECOND_LINES = (
    "Hands open to the daylight", "One mindful breath begins", "The garden calls you near",
    "Footsteps find the open air", "A book waits without a sound", "Water catches morning light",
    "The table holds a story", "A calm hour makes room", "Your future self smiles back",
    "The world is more than alerts",
)
HAIKU_THIRD_LINES = (
    "Room to breathe again", "Choose the present now", "Let the next hour bloom", "Rest becomes a promise",
    "One small boundary", "The day belongs to you", "Silence grows a little", "Real life answers back",
    "A gentler tomorrow", "Begin where you are",
)

ACTION_STARTS = (
    "Read a few pages", "Walk outside", "Drink a full glass of water", "Stretch by an open window",
    "Write one sentence in a journal", "Tidy one small surface", "Make a warm drink", "Call someone you miss",
    "Put on one favorite song", "Take five slow breaths",
)
ACTION_ENDS = (
    "for 20 phone-free minutes.", "for 15 minutes before your next unlock.", "before opening any social app.",
    "with your phone in another room.", "before checking notifications again.", "while your phone stays face down.",
    "before your next meal.", "during the next natural pause in your day.", "before you start tomorrow's first task.",
    "and notice how your body feels afterward.",
)
PLEDGE_STARTS = (
    "I will protect", "I will make space for", "I will begin with", "I will choose", "I will return to",
    "I will give myself", "I will notice", "I will keep", "I will practice", "I will create",
)
PLEDGE_ENDS = (
    "one quiet moment before I scroll.", "a phone-free start to my next task.", "one real-world connection today.",
    "rest before late-night scrolling.", "a small boundary that supports my goal.", "the work that matters before my feed.",
    "my breath before I open another app.", "a little more room for presence.", "an intentional pause between notifications.",
    "a kinder relationship with my attention.",
)


def format_screen_time(minutes):
    if minutes is None:
        return None
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h {remaining_minutes:02d}m" if hours else f"{remaining_minutes}m"


def _pick_pair(starts, ends, index):
    pair_index = index % (len(starts) * len(ends))
    return f"{starts[pair_index // len(ends)]} {ends[pair_index % len(ends)]}"


def _generate_haiku(index):
    first = HAIKU_FIRST_LINES[index % len(HAIKU_FIRST_LINES)]
    second = HAIKU_SECOND_LINES[(index // len(HAIKU_FIRST_LINES)) % len(HAIKU_SECOND_LINES)]
    third = HAIKU_THIRD_LINES[
        (index // (len(HAIKU_FIRST_LINES) * len(HAIKU_SECOND_LINES))) % len(HAIKU_THIRD_LINES)
    ]
    return f"{first}\n{second}\n{third}"


def generate_postcard(*, mood, goal, screen_time, has_report):
    """Generate a varied postcard; each cycle covers at least 100 combinations."""
    index = next(_generation_counter)
    reflection = " ".join((MOOD_OPENERS[mood], GOAL_LINES[goal], REPORT_LINES[has_report]))
    if screen_time:
        reflection += f" You logged {screen_time} today; tomorrow is a fresh chance to choose your attention."
    return {
        "mood": mood,
        "goal": goal,
        "screen_time": screen_time,
        "haiku": _generate_haiku(index),
        "reflection": reflection,
        "action": _pick_pair(ACTION_STARTS, ACTION_ENDS, index),
        "pledge": _pick_pair(PLEDGE_STARTS, PLEDGE_ENDS, index),
    }


def postcard_lines(postcard):
    return [
        "UNSCROLL — YOUR DIGITAL POSTCARD", "", f"Mood: {postcard['mood']}",
        f"Tomorrow's goal: {postcard['goal']}", f"Screen time: {postcard.get('screen_time') or 'Not provided'}", "",
        "TODAY'S HAIKU", postcard["haiku"], "", "YOUR REFLECTION", postcard["reflection"], "",
        "TINY ACTION", postcard["action"], "", "TOMORROW'S PLEDGE", postcard["pledge"],
    ]


def render_postcard_png(postcard):
    image = Image.new("RGB", (1400, 1800), "#0f172a")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 70, 1330, 1730), radius=48, fill="#172554", outline="#67e8f9", width=4)
    title_font = ImageFont.load_default(size=42)
    heading_font = ImageFont.load_default(size=25)
    body_font = ImageFont.load_default(size=21)
    y = 130
    for line in postcard_lines(postcard):
        font = title_font if line.startswith("UNSCROLL") else heading_font if line.isupper() else body_font
        color = "#67e8f9" if font != body_font else "#e2e8f0"
        for paragraph in line.splitlines() or [""]:
            for wrapped_line in wrap(paragraph, width=72) or [""]:
                draw.text((130, y), wrapped_line, fill=color, font=font)
                y += 42 if font == body_font else 50
        y += 12
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_postcard_pdf(postcard):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A5)
    width, height = A5
    pdf.setFillColor(HexColor("#0f172a"))
    pdf.rect(0, 0, width, height, stroke=0, fill=1)
    y = height - 42
    for line in postcard_lines(postcard):
        is_heading = line.startswith("UNSCROLL") or line.isupper()
        pdf.setFont("Helvetica-Bold" if is_heading else "Helvetica", 13 if line.startswith("UNSCROLL") else 10)
        pdf.setFillColor(HexColor("#67e8f9") if is_heading else HexColor("#e2e8f0"))
        for paragraph in line.splitlines() or [""]:
            for wrapped_line in wrap(paragraph, width=62) or [""]:
                if y < 42:
                    pdf.showPage()
                    pdf.setFillColor(HexColor("#0f172a"))
                    pdf.rect(0, 0, width, height, stroke=0, fill=1)
                    y = height - 42
                pdf.drawString(34, y, wrapped_line)
                y -= 17
        y -= 7
    pdf.save()
    return output.getvalue()
