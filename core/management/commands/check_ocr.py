from django.core.management.base import BaseCommand

from services.ocr_runtime import configure_pytesseract, resolve_tesseract


class Command(BaseCommand):
    help = "Check OCR dependency and native Tesseract availability without reading user data."

    def handle(self, *args, **options):
        runtime = resolve_tesseract()
        library_available = runtime.status != "library_unavailable"
        self.stdout.write(f"OCR Python dependency: {'available' if library_available else 'unavailable'}")
        self.stdout.write(f"Tesseract executable: {'available' if runtime.available else 'unavailable'}")
        if runtime.available:
            try:
                version = configure_pytesseract(runtime).get_tesseract_version()
                self.stdout.write(f"Tesseract version: {version}")
            except (RuntimeError, OSError):
                self.stdout.write("Tesseract version: runtime check failed")
