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

    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return None

    hours, remaining_minutes = divmod(minutes, 60)
    return (
        f"{hours}h {remaining_minutes:02d}m"
        if hours
        else f"{remaining_minutes}m"
    )




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


def render_postcard_png(postcard_data):
    """Render a premium BeyondScreen postcard as PNG bytes."""
    import io
    import os
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    width, height = 1600, 1000

    colors = {
        "background_top": (4, 13, 23),
        "background_bottom": (7, 20, 33),
        "panel": (10, 27, 42),
        "panel_soft": (13, 34, 51),
        "gold": (239, 174, 67),
        "gold_soft": (255, 208, 126),
        "teal": (57, 221, 213),
        "blue": (121, 156, 242),
        "purple": (189, 131, 239),
        "text": (248, 250, 252),
        "muted": (166, 179, 193),
        "muted_soft": (117, 133, 151),
        "line": (45, 63, 80),
    }

    def load_font(size, *, bold=False, serif=False, italic=False):
        windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"

        if serif:
            names = (
                ["georgiaz.ttf", "georgiai.ttf", "georgiab.ttf", "georgia.ttf"]
                if italic
                else ["georgiab.ttf", "georgia.ttf"]
            )
            linux = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            ]
        elif bold:
            names = ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"]
            linux = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            ]
        else:
            names = ["segoeui.ttf", "arial.ttf"]
            linux = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            ]

        candidates = [windows / name for name in names]
        candidates.extend(Path(item) for item in linux)

        for candidate in candidates:
            try:
                if candidate.exists():
                    return ImageFont.truetype(str(candidate), size=size)
            except (OSError, ValueError):
                continue

        return ImageFont.load_default()

    def text_width(draw, text, font):
        box = draw.textbbox((0, 0), str(text), font=font)
        return box[2] - box[0]

    def wrap_text(draw, text, font, max_width, max_lines=None):
        paragraphs = str(text or "").replace("\r", "").split("\n")
        lines = []

        for paragraph in paragraphs:
            words = paragraph.split()

            if not words:
                if lines and (max_lines is None or len(lines) < max_lines):
                    lines.append("")
                continue

            current = ""

            for word in words:
                candidate = word if not current else f"{current} {word}"

                if text_width(draw, candidate, font) <= max_width:
                    current = candidate
                    continue

                if current:
                    lines.append(current)
                    current = word
                else:
                    lines.append(word)
                    current = ""

                if max_lines is not None and len(lines) >= max_lines:
                    break

            if max_lines is not None and len(lines) >= max_lines:
                break

            if current:
                lines.append(current)

            if max_lines is not None and len(lines) >= max_lines:
                break

        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]

        if max_lines is not None and len(lines) == max_lines:
            combined = " ".join(paragraphs).strip()
            visible = " ".join(lines).strip()

            if len(visible) < len(combined):
                last = lines[-1].rstrip(" .")
                while last and text_width(draw, f"{last}…", font) > max_width:
                    last = last[:-1].rstrip()
                lines[-1] = f"{last}…"

        return lines

    def draw_lines(draw, lines, x, y, font, fill, line_gap, *, anchor=None):
        current_y = y

        for line in lines:
            draw.text(
                (x, current_y),
                line,
                font=font,
                fill=fill,
                anchor=anchor,
            )
            current_y += line_gap

        return current_y

    def rounded_panel(draw, box, radius, fill, outline, width_px=2):
        draw.rounded_rectangle(
            box,
            radius=radius,
            fill=fill,
            outline=outline,
            width=width_px,
        )

    image = Image.new("RGB", (width, height), colors["background_top"])
    pixels = image.load()

    for y in range(height):
        ratio = y / max(1, height - 1)
        top = colors["background_top"]
        bottom = colors["background_bottom"]
        shade = tuple(
            int(top[index] + (bottom[index] - top[index]) * ratio)
            for index in range(3)
        )

        for x in range(width):
            pixels[x, y] = shade

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (-180, -230, 620, 570),
        fill=(*colors["gold"], 54),
    )
    glow_draw.ellipse(
        (1000, 560, 1780, 1340),
        fill=(*colors["teal"], 42),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    image = Image.alpha_composite(image.convert("RGBA"), glow)

    draw = ImageDraw.Draw(image)

    card = (76, 70, 1524, 930)
    rounded_panel(
        draw,
        card,
        38,
        (*colors["panel"], 252),
        (*colors["line"], 255),
        3,
    )

    draw.line(
        (108, 72, 415, 72),
        fill=(*colors["gold"], 220),
        width=4,
    )
    draw.line(
        (1185, 928, 1490, 928),
        fill=(*colors["teal"], 180),
        width=3,
    )

    brand_font = load_font(25, bold=True)
    tiny_font = load_font(18)
    label_font = load_font(20, bold=True)
    chip_font = load_font(23, bold=True)
    body_font = load_font(27)
    body_bold_font = load_font(28, bold=True)
    haiku_font = load_font(56, serif=True, italic=True)
    pledge_font = load_font(29, bold=True)
    footer_font = load_font(18)

    draw.text(
        (118, 112),
        "BEYONDSCREEN",
        font=brand_font,
        fill=colors["gold"],
    )
    draw.text(
        (118, 149),
        "YOUR TIME. RECLAIMED.",
        font=tiny_font,
        fill=colors["muted_soft"],
    )

    stamp = (1372, 105, 1468, 201)
    rounded_panel(
        draw,
        stamp,
        18,
        (*colors["gold"], 20),
        (*colors["gold"], 120),
        2,
    )
    draw.text(
        (1420, 139),
        "✦",
        font=load_font(31, bold=True),
        fill=colors["gold"],
        anchor="mm",
    )
    draw.text(
        (1420, 178),
        "MINDFUL",
        font=load_font(13, bold=True),
        fill=colors["muted"],
        anchor="mm",
    )

    content_left = 118
    content_width = 835

    draw.text(
        (content_left, 238),
        "A NOTE TO YOURSELF",
        font=label_font,
        fill=colors["teal"],
    )

    haiku = str(
        postcard_data.get("haiku")
        or "A quieter moment begins here."
    ).strip()
    haiku_lines = wrap_text(
        draw,
        haiku,
        haiku_font,
        content_width,
        max_lines=4,
    )
    haiku_bottom = draw_lines(
        draw,
        haiku_lines,
        content_left,
        280,
        haiku_font,
        colors["text"],
        72,
    )

    reflection_y = min(max(haiku_bottom + 28, 500), 590)

    draw.text(
        (content_left, reflection_y),
        "REFLECTION",
        font=label_font,
        fill=colors["gold"],
    )

    reflection = str(
        postcard_data.get("reflection")
        or "Notice the space that appears when attention returns to you."
    ).strip()
    reflection_lines = wrap_text(
        draw,
        reflection,
        body_font,
        content_width,
        max_lines=4,
    )
    draw_lines(
        draw,
        reflection_lines,
        content_left,
        reflection_y + 39,
        body_font,
        colors["muted"],
        39,
    )

    divider_x = 1000
    draw.line(
        (divider_x, 235, divider_x, 825),
        fill=(*colors["line"], 255),
        width=2,
    )

    right_x = 1048
    right_w = 394

    draw.text(
        (right_x, 238),
        "TODAY'S SNAPSHOT",
        font=label_font,
        fill=colors["gold"],
    )

    chip_y = 284
    chip_gap = 76
    chips = [
        ("MOOD", postcard_data.get("mood") or "Reflective", colors["teal"]),
        ("INTENTION", postcard_data.get("goal") or "Mindful progress", colors["gold"]),
        ("SCREEN TIME", postcard_data.get("screen_time") or "0m", colors["blue"]),
    ]

    for index, (label, value, accent) in enumerate(chips):
        y = chip_y + index * chip_gap
        rounded_panel(
            draw,
            (right_x, y, right_x + right_w, y + 58),
            16,
            (*colors["panel_soft"], 245),
            (*accent, 70),
            2,
        )
        draw.text(
            (right_x + 18, y + 13),
            label,
            font=load_font(14, bold=True),
            fill=colors["muted_soft"],
        )
        value_text = str(value)
        value_font = chip_font

        while (
            text_width(draw, value_text, value_font) > right_w - 155
            and getattr(value_font, "size", 20) > 15
        ):
            value_font = load_font(
                getattr(value_font, "size", 20) - 1,
                bold=True,
            )

        draw.text(
            (right_x + right_w - 18, y + 29),
            value_text,
            font=value_font,
            fill=colors["text"],
            anchor="rm",
        )

    action_y = 545
    rounded_panel(
        draw,
        (right_x, action_y, right_x + right_w, action_y + 122),
        18,
        (*colors["panel_soft"], 245),
        (*colors["gold"], 62),
        2,
    )
    draw.text(
        (right_x + 19, action_y + 17),
        "NEXT ACTION",
        font=load_font(15, bold=True),
        fill=colors["gold"],
    )
    action_lines = wrap_text(
        draw,
        postcard_data.get("action") or "Take one small step away from the screen.",
        body_bold_font,
        right_w - 38,
        max_lines=2,
    )
    draw_lines(
        draw,
        action_lines,
        right_x + 19,
        action_y + 47,
        body_bold_font,
        colors["text"],
        35,
    )

    pledge_y = 690
    rounded_panel(
        draw,
        (right_x, pledge_y, right_x + right_w, pledge_y + 145),
        18,
        (24, 30, 47, 245),
        (*colors["purple"], 70),
        2,
    )
    draw.text(
        (right_x + 19, pledge_y + 17),
        "YOUR PLEDGE",
        font=load_font(15, bold=True),
        fill=colors["purple"],
    )
    pledge_lines = wrap_text(
        draw,
        f'“{postcard_data.get("pledge") or "I will protect one quiet moment for myself."}”',
        pledge_font,
        right_w - 38,
        max_lines=3,
    )
    draw_lines(
        draw,
        pledge_lines,
        right_x + 19,
        pledge_y + 48,
        pledge_font,
        colors["text"],
        34,
    )

    footer_y = 875
    draw.text(
        (118, footer_y),
        "A mindful reflection, preserved by BeyondScreen.",
        font=footer_font,
        fill=colors["muted_soft"],
    )
    draw.text(
        (1470, footer_y),
        "beyond the screen",
        font=footer_font,
        fill=colors["teal"],
        anchor="ra",
    )

    output = io.BytesIO()
    image.convert("RGB").save(
        output,
        format="PNG",
        optimize=True,
    )
    return output.getvalue()



def render_postcard_pdf(postcard_data):
    """Render the premium BeyondScreen postcard inside a print-ready PDF."""
    import io

    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    png_bytes = render_postcard_png(postcard_data)

    page_width = 720
    page_height = 450

    output = io.BytesIO()
    pdf = canvas.Canvas(
        output,
        pagesize=(page_width, page_height),
        pageCompression=1,
    )
    pdf.setTitle("BeyondScreen Mindful Postcard")
    pdf.setAuthor("BeyondScreen")
    pdf.setSubject("A mindful digital-wellbeing reflection")
    pdf.drawImage(
        ImageReader(io.BytesIO(png_bytes)),
        0,
        0,
        width=page_width,
        height=page_height,
        preserveAspectRatio=True,
        mask="auto",
    )
    pdf.showPage()
    pdf.save()
    return output.getvalue()


# GOAL_RESCUE_FOUNDATION
def _goal_rescue_number(value):
    """Return a Decimal-like value without unnecessary trailing zeroes."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    if number.is_integer():
        return str(int(number))

    return f"{number:.2f}".rstrip("0").rstrip(".")


def _goal_rescue_progress_phrase(progress_unit, progress_value):
    value = _goal_rescue_number(progress_value)

    try:
        numeric_value = float(progress_value)
    except (TypeError, ValueError):
        numeric_value = None

    singular = numeric_value == 1

    if progress_unit == "minutes":
        return (
            f"{value} focused minute"
            if singular
            else f"{value} focused minutes"
        )

    if progress_unit == "sessions":
        return (
            "1 session completed"
            if singular
            else f"{value} sessions completed"
        )

    if progress_unit == "questions":
        return (
            "1 question solved"
            if singular
            else f"{value} questions solved"
        )

    if progress_unit == "pages":
        return (
            "1 page completed"
            if singular
            else f"{value} pages completed"
        )

    if progress_unit == "tasks":
        return (
            "1 task completed"
            if singular
            else f"{value} tasks completed"
        )

    if progress_unit == "workouts":
        return (
            "1 workout completed"
            if singular
            else f"{value} workouts completed"
        )

    return f"{value} {progress_unit}".strip()



def build_goal_rescue(user, screen_time_minutes):
    """
    Match a realistic slice of today's screen time to one Goal DNA action.

    The response also includes the complete three-step Goal DNA ladder so
    the Summary can show what the user's time could have moved forward.
    Goal Rescue recommends actions only; it never records completion.
    """
    try:
        minutes = max(0, int(screen_time_minutes))
    except (TypeError, ValueError):
        minutes = 0

    if not getattr(user, "is_authenticated", False):
        return {
            "status": "sign_in",
            "screen_time_minutes": minutes,
        }

    from .models import UserGoal

    goal = (
        UserGoal.objects.filter(
            user=user,
            status=UserGoal.STATUS_ACTIVE,
            is_primary=True,
        )
        .prefetch_related("actions")
        .first()
    )

    if goal is None:
        paused_goal = (
            UserGoal.objects.filter(
                user=user,
                status=UserGoal.STATUS_PAUSED,
                is_primary=True,
            )
            .order_by("-updated_at")
            .first()
        )

        if paused_goal is not None:
            return {
                "status": "paused_goal",
                "goal_id": paused_goal.id,
                "goal_title": paused_goal.title,
                "screen_time_minutes": minutes,
            }

        return {
            "status": "no_goal",
            "screen_time_minutes": minutes,
        }

    actions = sorted(
        goal.actions.all(),
        key=lambda action: action.duration_minutes,
    )

    if not actions:
        return {
            "status": "incomplete_goal",
            "goal_title": goal.title,
            "screen_time_minutes": minutes,
        }

    if minutes <= 0:
        return {
            "status": "no_screen_time",
            "goal_title": goal.title,
            "screen_time_minutes": minutes,
        }

    smallest_action = actions[0]
    realistic_slice = max(1, round(minutes * 0.10))
    rescue_budget = max(
        smallest_action.duration_minutes,
        realistic_slice,
    )

    eligible_actions = [
        action
        for action in actions
        if action.duration_minutes <= rescue_budget
    ]

    if eligible_actions:
        selected_action = eligible_actions[-1]
        selection_reason = (
            "This is the largest step in your Goal DNA that fits a "
            "small, realistic slice of today's screen time."
        )
    else:
        selected_action = smallest_action
        selection_reason = (
            "This is the smallest meaningful step in your Goal DNA, "
            "so it is the safest place to begin."
        )

    size_details = {
        "minimum": {
            "label": "Small Step",
            "context": "For a difficult or busy day",
        },
        "standard": {
            "label": "Regular Step",
            "context": "For a normal day",
        },
        "deep": {
            "label": "Bigger Step",
            "context": "When you have more time and energy",
        },
    }

    goal_actions = []

    for action in actions:
        detail = size_details.get(
            action.size,
            {
                "label": action.get_size_display(),
                "context": "A step that moves your goal forward",
            },
        )

        goal_actions.append(
            {
                "id": action.id,
                "title": action.title,
                "minutes": action.duration_minutes,
                "size": action.size,
                "size_label": detail["label"],
                "context": detail["context"],
                "progress_phrase": _goal_rescue_progress_phrase(
                    goal.progress_unit,
                    action.progress_value,
                ),
                "is_selected": action.id == selected_action.id,
            }
        )

    selected_detail = size_details.get(
        selected_action.size,
        {
            "label": selected_action.get_size_display(),
            "context": "A step that moves your goal forward",
        },
    )

    return {
        "status": "ready",
        "goal_title": goal.title,
        "goal_reason": goal.why_it_matters,
        "current_focus": goal.current_focus,
        "action_id": selected_action.id,
        "action_title": selected_action.title,
        "action_minutes": selected_action.duration_minutes,
        "action_size": selected_action.size,
        "action_size_label": selected_detail["label"],
        "action_context": selected_detail["context"],
        "progress_phrase": _goal_rescue_progress_phrase(
            goal.progress_unit,
            selected_action.progress_value,
        ),
        "selection_reason": selection_reason,
        "screen_time_minutes": minutes,
        "screen_time_display": format_screen_time(minutes),
        "goal_actions": goal_actions,
        "is_completed": False,
    }
