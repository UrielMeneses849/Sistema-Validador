from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from app.core.config import OCR_MAX_IMAGE_PIXELS


SUPPORTED_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
MAX_IMAGE_SIDE = 12_000
MIN_OCR_LONG_EDGE = 1_800
MAX_UPSCALE_FACTOR = 2.0


class ImageValidationError(ValueError):
    pass


def inspect_image_content(content: bytes) -> tuple[str, int, int]:
    """Valida el contenido decodificable sin conservar ni transformar el original."""
    try:
        with Image.open(BytesIO(content)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
            if image_format not in SUPPORTED_IMAGE_FORMATS:
                raise ImageValidationError("La imagen debe tener contenido JPEG, PNG o WEBP válido.")
            if width <= 0 or height <= 0:
                raise ImageValidationError("La imagen no tiene dimensiones válidas.")
            if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE or width * height > OCR_MAX_IMAGE_PIXELS:
                raise ImageValidationError(
                    f"La imagen excede el límite seguro de {OCR_MAX_IMAGE_PIXELS:,} píxeles."
                )
            image.verify()
    except ImageValidationError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ImageValidationError("El archivo está corrupto o no contiene una imagen válida.") from exc
    return image_format, width, height


def load_oriented_rgb(source_path: str | Path) -> Image.Image:
    """Carga una copia independiente, corrige EXIF y normaliza el espacio de color."""
    try:
        with Image.open(source_path) as source:
            if source.width * source.height > OCR_MAX_IMAGE_PIXELS:
                raise ImageValidationError("La imagen excede el límite seguro de píxeles.")
            return ImageOps.exif_transpose(source).convert("RGB").copy()
    except ImageValidationError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageValidationError("No fue posible abrir la imagen para preprocesarla.") from exc


def to_grayscale(image: Image.Image) -> Image.Image:
    return ImageOps.grayscale(image)


def improve_contrast_and_sharpness(image: Image.Image) -> Image.Image:
    contrasted = ImageOps.autocontrast(image, cutoff=1)
    contrasted = ImageEnhance.Contrast(contrasted).enhance(1.15)
    return contrasted.filter(ImageFilter.UnsharpMask(radius=1.2, percent=125, threshold=3))


def upscale_small_document(image: Image.Image) -> Image.Image:
    long_edge = max(image.size)
    if long_edge >= MIN_OCR_LONG_EDGE:
        return image.copy()
    scale = min(MAX_UPSCALE_FACTOR, MIN_OCR_LONG_EDGE / max(long_edge, 1))
    target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    if target[0] * target[1] > OCR_MAX_IMAGE_PIXELS:
        return image.copy()
    return image.resize(target, Image.Resampling.LANCZOS)


def preprocess_image(source_path: str | Path, destination_path: str | Path) -> Path:
    """Genera una copia PNG temporal optimizada; nunca modifica el original."""
    oriented = load_oriented_rgb(source_path)
    grayscale = to_grayscale(oriented)
    enhanced = improve_contrast_and_sharpness(grayscale)
    prepared = upscale_small_document(enhanced)
    destination = Path(destination_path)
    prepared.save(destination, format="PNG", optimize=True)
    return destination
