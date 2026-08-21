"""Multi-pass OCR and structured screenshot extraction orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
import time

from .image_preprocessing import ImageTooLarge, InvalidImage, build_variants, load_safe_image
from .ocr_runtime import OCR_EXECUTION_FAILED, configure_pytesseract, resolve_tesseract

logger = logging.getLogger(__name__)


@dataclass
class OCRCandidate:
    text: str
    variant: str
    psm: int
    score: int


@dataclass
class ScreenshotExtractionResult:
    status: str
    provider: str = "UNKNOWN"
    confidence: str = "NONE"
    total_screen_minutes: int | None = None
    recognized_app_total_minutes: int = 0
    apps: list[dict] = field(default_factory=list)
    pickups: int | None = None
    unlocks: int | None = None
    notifications: int | None = None
    sessions: int | None = None
    longest_session_minutes: int | None = None
    first_use_time: str | None = None
    last_use_time: str | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def has_analytics(self):
        return self.total_screen_minutes is not None or bool(self.apps) or any(
            value is not None for value in (self.pickups, self.unlocks, self.notifications, self.sessions)
        )

    def to_payload(self):
        platform = {
            "ANDROID_DIGITAL_WELLBEING": "android",
            "SAMSUNG_DIGITAL_WELLBEING": "android",
            "IOS_SCREEN_TIME": "ios",
            "GENERIC_USAGE_ANALYTICS": "generic",
        }.get(self.provider, "unknown")
        source = {
            "ANDROID_DIGITAL_WELLBEING": "android_digital_wellbeing",
            "SAMSUNG_DIGITAL_WELLBEING": "android_digital_wellbeing",
            "IOS_SCREEN_TIME": "ios_screen_time",
            "GENERIC_USAGE_ANALYTICS": "generic_ocr",
        }.get(self.provider, "unknown")
        return {
            "status": self.status,
            "source_type": source,
            "platform": platform,
            "detection_confidence": self.confidence.lower(),
            "total_minutes": self.total_screen_minutes,
            "total_screen_time": self.total_screen_minutes,
            "recognized_app_total_minutes": self.recognized_app_total_minutes,
            "apps": self.apps,
            "pickups": self.pickups,
            "unlocks": self.unlocks,
            "notifications": self.notifications,
            "sessions": self.sessions,
            "longest_session_minutes": self.longest_session_minutes,
            "first_use_time": self.first_use_time,
            "last_use_time": self.last_use_time,
            "provider": self.provider,
            "confidence": self.confidence,
            "warnings": self.warnings,
            "processing_metadata": self.metadata,
        }


ANALYTICS_TERMS = (
    "screen time", "digital wellbeing", "daily average", "most used", "pickups",
    "unlocks", "notifications", "app timers", "dashboard", "focus mode",
)


def score_ocr_text(text: str) -> int:
    normalized = text.casefold()
    alnum = sum(char.isalnum() for char in text)
    if alnum < 4:
        return -50
    keywords = sum(1 for term in ANALYTICS_TERMS if term in normalized)
    durations = len(re.findall(r"\b\d{1,3}\s*(?:h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?)\b", normalized))
    lines = sum(1 for line in text.splitlines() if line.strip())
    garbage = sum(1 for char in text if not (char.isalnum() or char.isspace() or char in ":.-&/()"))
    return keywords * 18 + durations * 9 + min(lines, 20) + min(alnum // 25, 12) - garbage // 4


def _run_candidate(engine, variant, psm):
    text = engine.image_to_string(variant.image, config=f"--oem 3 --psm {psm}", timeout=15)
    return OCRCandidate(text=text or "", variant=variant.name, psm=psm, score=score_ocr_text(text or ""))


def extract_screenshot(uploaded_file, parse_text) -> ScreenshotExtractionResult:
    started = time.monotonic()
    try:
        image = load_safe_image(uploaded_file)
    except ImageTooLarge:
        return ScreenshotExtractionResult("IMAGE_TOO_LARGE")
    except InvalidImage:
        return ScreenshotExtractionResult("INVALID_IMAGE")

    runtime = resolve_tesseract()
    if not runtime.available:
        return ScreenshotExtractionResult(runtime.status.upper())
    engine = configure_pytesseract(runtime)
    variants = build_variants(image)
    candidates = []
    try:
        # Fast path: natural layout and sparse UI text.
        for psm in (6, 11):
            candidates.append(_run_candidate(engine, variants[0], psm))
        best = max(candidates, key=lambda item: item.score)
        # Escalate only when the fast path lacks strong analytics evidence.
        if best.score < 65:
            for variant in variants[1:3]:
                candidates.append(_run_candidate(engine, variant, 11))
            best = max(candidates, key=lambda item: item.score)
    except (RuntimeError, OSError, TimeoutError) as exc:
        logger.warning("ocr.status=execution_failed error_type=%s", type(exc).__name__)
        return ScreenshotExtractionResult(OCR_EXECUTION_FAILED.upper())
    except Exception as exc:  # Provider/library failures vary by pytesseract release.
        logger.exception("ocr.status=unexpected_failure error_type=%s", type(exc).__name__)
        return ScreenshotExtractionResult(OCR_EXECUTION_FAILED.upper())

    parsed = parse_text(best.text)
    result = ScreenshotExtractionResult(
        status="SUCCESS" if parsed.get("has_analytics") else "NO_ANALYTICS_FOUND",
        provider=parsed["provider"], confidence=parsed["confidence"],
        total_screen_minutes=parsed.get("total_minutes"), apps=parsed.get("apps", []),
        recognized_app_total_minutes=sum(item["minutes"] for item in parsed.get("apps", [])),
        pickups=parsed.get("pickups"), unlocks=parsed.get("unlocks"),
        notifications=parsed.get("notifications"), sessions=parsed.get("sessions"),
        longest_session_minutes=parsed.get("longest_session_minutes"),
        first_use_time=parsed.get("first_use_time"), last_use_time=parsed.get("last_use_time"),
        warnings=parsed.get("warnings", []),
        metadata={"variant": best.variant, "psm": best.psm, "candidate_count": len(candidates)},
    )
    logger.info(
        "ocr.status=%s ocr.provider=%s ocr.confidence=%s ocr.fields_detected=%s ocr.duration_ms=%s",
        result.status.lower(), result.provider.lower(), result.confidence.lower(),
        int(result.total_screen_minutes is not None) + len(result.apps) + int(result.pickups is not None)
        + int(result.notifications is not None), int((time.monotonic() - started) * 1000),
    )
    return result
