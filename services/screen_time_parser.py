try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None
    Image = None
    TESSERACT_AVAILABLE = False
import re
import logging
from typing import Dict, List, Union, Optional

logger = logging.getLogger(__name__)


def parse_screen_time_report(uploaded_file) -> Optional[Dict[str, Union[int, List[Dict[str, int]]]]]:
    """
    Parse Android Digital Wellbeing screenshot to extract screen time data.

    Args:
        uploaded_file: Django UploadedFile object (or file-like)

    Returns:
        Dict with keys:
            - total_screen_time: int (minutes)
            - apps: List of dicts with 'name' (str) and 'minutes' (int)
        Returns None if parsing fails or Tesseract is not available.
    """
    if not TESSERACT_AVAILABLE:
        return None
    try:
        # Open image
        image = Image.open(uploaded_file)

        # Perform OCR
        text = pytesseract.image_to_string(image)

        # Parse total screen time
        total_minutes = _extract_total_screen_time(text)
        if total_minutes is None:
            return None

        # Parse apps
        apps = _extract_apps(text)
        if apps is None:
            apps = []  # Allow empty apps list if not found

        return {
            "total_screen_time": total_minutes,
            "apps": apps
        }
    except Exception as exc:
        # Any error results in fallback to manual entry
        logger.warning("Screen-time OCR failed (%s).", type(exc).__name__)
        return None


def _extract_total_screen_time(text: str) -> Optional[int]:
    """Extract total screen time in minutes from OCR text."""
    # Normalize text
    lines = [line.strip() for line in text.split('\n') if line.strip()]

    # Look for patterns like "Total Screen Time" followed by time like "3h 41m"
    for i, line in enumerate(lines):
        if re.search(r'total\s+screen\s+time', line, re.IGNORECASE):
            # Check next line for time
            if i + 1 < len(lines):
                time_line = lines[i + 1]
                minutes = _parse_time_string(time_line)
                if minutes is not None:
                    return minutes
            # Also check same line after colon
            match = re.search(r'Total\s+Screen\s+Time[:\s]+([0-9]+h?\s*[0-9]*m?)', line, re.IGNORECASE)
            if match:
                minutes = _parse_time_string(match.group(1))
                if minutes is not None:
                    return minutes
    return None


def _extract_apps(text: str) -> Optional[List[Dict[str, int]]]:
    """Extract app usage data from OCR text."""
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    apps = []

    # Find the Apps section
    apps_start = -1
    for i, line in enumerate(lines):
        if re.search(r'^Apps?$', line, re.IGNORECASE):
            apps_start = i
            break

    if apps_start == -1:
        return None

    # Process lines after "Apps"
    i = apps_start + 1
    while i < len(lines):
        # Skip empty lines
        if not lines[i]:
            i += 1
            continue

        # Potential app name line
        name = lines[i]
        i += 1

        # Look for time line
        if i < len(lines):
            time_line = lines[i]
            minutes = _parse_time_string(time_line)
            if minutes is not None:
                apps.append({"name": name, "minutes": minutes})
                i += 1
                continue
            # If not a time, maybe the name was actually part of previous? Reset
            # We'll treat this as not an app entry and continue
        # If we couldn't parse time, break or continue?
        # For robustness, break
        break

    return apps if apps else None


def _parse_time_string(time_str: str) -> Optional[int]:
    """Convert time string like '3h 41m' or '45m' to minutes."""
    if not time_str:
        return None

    time_str = time_str.strip().lower()
    total_minutes = 0

    # Match hours
    hour_match = re.search(r'(\d+)\s*h', time_str)
    if hour_match:
        total_minutes += int(hour_match.group(1)) * 60

    # Match minutes
    min_match = re.search(r'(\d+)\s*m', time_str)
    if min_match:
        total_minutes += int(min_match.group(1))

    # If we found either hours or minutes, return total
    if hour_match or min_match:
        return total_minutes

    # If just a number (assume minutes)
    if re.fullmatch(r'\d+', time_str):
        return int(time_str)

    return None
