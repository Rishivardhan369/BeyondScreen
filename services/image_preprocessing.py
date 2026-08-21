"""Bounded, in-memory preprocessing for phone analytics screenshots."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageEnhance, ImageOps, ImageStat, UnidentifiedImageError


MAX_IMAGE_PIXELS = 40_000_000
MAX_OCR_EDGE = 5000
SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP"}
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class InvalidImage(Exception):
    pass


class ImageTooLarge(InvalidImage):
    pass


@dataclass(frozen=True)
class ImageVariant:
    name: str
    image: Image.Image


def load_safe_image(uploaded_file) -> Image.Image:
    try:
        uploaded_file.seek(0)
        payload = uploaded_file.read()
        if not payload:
            raise InvalidImage("empty")
        with Image.open(BytesIO(payload)) as probe:
            if probe.format not in SUPPORTED_FORMATS:
                raise InvalidImage("unsupported")
            probe.verify()
        with Image.open(BytesIO(payload)) as source:
            source.load()
            oriented = ImageOps.exif_transpose(source)
            if oriented.width <= 0 or oriented.height <= 0:
                raise InvalidImage("dimensions")
            if oriented.width * oriented.height > MAX_IMAGE_PIXELS:
                raise ImageTooLarge("pixels")
            if oriented.mode in ("RGBA", "LA") or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                canvas = Image.new("RGBA", rgba.size, "white")
                canvas.alpha_composite(rgba)
                oriented = canvas.convert("RGB")
            else:
                oriented = oriented.convert("RGB")
            return oriented.copy()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageTooLarge("decompression")
    except (UnidentifiedImageError, OSError, TypeError, ValueError) as exc:
        raise InvalidImage("corrupt") from exc


def approximate_luminance(image: Image.Image) -> float:
    thumb = image.copy()
    thumb.thumbnail((256, 256))
    return float(ImageStat.Stat(thumb.convert("L")).mean[0])


def _upscale(image: Image.Image, factor: int) -> Image.Image:
    factor = min(factor, max(1, MAX_OCR_EDGE // max(image.size)))
    if factor <= 1:
        return image.copy()
    return image.resize((image.width * factor, image.height * factor), Image.Resampling.LANCZOS)


def build_variants(image: Image.Image) -> list[ImageVariant]:
    """Return a small deterministic set; escalation remains capped at four variants."""
    variants = [ImageVariant("original_rgb", image)]
    gray = image.convert("L")
    auto = ImageOps.autocontrast(gray, cutoff=1)
    enhanced = ImageEnhance.Sharpness(ImageEnhance.Contrast(auto).enhance(1.7)).enhance(1.4)
    variants.append(ImageVariant("enhanced_gray", enhanced))
    if max(image.size) < 1800:
        factor = 3 if max(image.size) < 900 else 2
        variants.append(ImageVariant(f"upscaled_{factor}x", _upscale(enhanced, factor)))
    else:
        threshold = enhanced.point(lambda value: 255 if value > 155 else 0)
        variants.append(ImageVariant("binarized", threshold))
    if approximate_luminance(image) < 105:
        variants.append(ImageVariant("dark_mode_inverted", ImageOps.invert(enhanced)))
    return variants[:4]
