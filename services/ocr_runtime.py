"""Safe discovery and execution boundary for optional native OCR."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil


OCR_AVAILABLE = "available"
OCR_LIBRARY_UNAVAILABLE = "library_unavailable"
OCR_ENGINE_UNAVAILABLE = "engine_unavailable"
OCR_EXECUTION_FAILED = "execution_failed"


@dataclass(frozen=True)
class OCRRuntime:
    status: str
    command: str | None = None
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.status == OCR_AVAILABLE


def resolve_tesseract() -> OCRRuntime:
    """Resolve Tesseract without exposing its path outside this service."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return OCRRuntime(OCR_LIBRARY_UNAVAILABLE)

    configured = os.environ.get("TESSERACT_CMD", "").strip()
    candidates = []
    if configured:
        candidates.append(configured)
    discovered = shutil.which("tesseract")
    if discovered:
        candidates.append(discovered)
    if os.name == "nt":
        candidates.extend([
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ])
    for candidate in candidates:
        if Path(candidate).is_file():
            return OCRRuntime(OCR_AVAILABLE, str(Path(candidate)))
    return OCRRuntime(OCR_ENGINE_UNAVAILABLE)


def configure_pytesseract(runtime: OCRRuntime):
    if not runtime.available:
        return None
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = runtime.command
    return pytesseract
