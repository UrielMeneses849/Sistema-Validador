from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from app.core.config import OCR_MAX_IMAGE_PIXELS


SUPPORTED_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
MAX_IMAGE_SIDE = 12_000
MIN_OCR_LONG_EDGE = 1_800
MAX_UPSCALE_FACTOR = 2.0


class ImageValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ImageVariantSet:
    """Representaciones locales del mismo original, todas con igual geometría."""

    original_path: Path
    variants: dict[str, Path]
    width: int
    height: int
    oriented_path: Path | None = None
    perspective_path: Path | None = None
    rotation_degrees: int = 0
    deskew_degrees: float = 0.0
    perspective_corrected: bool = False
    diagnostics: list[str] = field(default_factory=list)


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


def _pillow_shadow_normalization(grayscale: Image.Image) -> Image.Image:
    """Aclara sombras lentas sin requerir NumPy/OpenCV."""
    background = grayscale.filter(ImageFilter.GaussianBlur(radius=max(8, min(grayscale.size) / 80)))
    return ImageOps.autocontrast(Image.blend(grayscale, ImageOps.autocontrast(background), 0.12), cutoff=1)


def _pillow_adaptive_like(grayscale: Image.Image) -> Image.Image:
    local_background = grayscale.filter(ImageFilter.GaussianBlur(radius=10))
    difference = Image.frombytes(
        "L",
        grayscale.size,
        bytes(max(0, min(255, pixel - background + 190)) for pixel, background in zip(
            grayscale.tobytes(), local_background.tobytes(), strict=True
        )),
    )
    return difference.point(lambda value: 255 if value > 172 else 0)


def _opencv_geometry(image: Image.Image) -> tuple[Image.Image, float, bool, list[str]]:
    """Corrige perspectiva y skew cuando cv2 está instalado; nunca es obligatorio."""
    diagnostics: list[str] = []
    try:
        import cv2
        import numpy as np
    except ImportError:
        return image, 0.0, False, ["OpenCV no está instalado; se usa geometría Pillow sin perspectiva/deskew avanzado."]

    array = np.asarray(image)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    image_area = float(image.width * image.height)
    perspective_corrected = False
    corrected = array
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(polygon) != 4 or cv2.contourArea(polygon) < image_area * 0.38:
            continue
        points = polygon.reshape(4, 2).astype("float32")
        point_sum = points.sum(axis=1)
        point_diff = np.diff(points, axis=1).reshape(-1)
        ordered = np.array([
            points[np.argmin(point_sum)], points[np.argmin(point_diff)],
            points[np.argmax(point_sum)], points[np.argmax(point_diff)],
        ], dtype="float32")
        top_left, top_right, bottom_right, bottom_left = ordered
        target_width = int(max(np.linalg.norm(bottom_right - bottom_left), np.linalg.norm(top_right - top_left)))
        target_height = int(max(np.linalg.norm(top_right - bottom_right), np.linalg.norm(top_left - bottom_left)))
        if target_width < 200 or target_height < 200:
            continue
        target = np.array(
            [[0, 0], [target_width - 1, 0], [target_width - 1, target_height - 1], [0, target_height - 1]],
            dtype="float32",
        )
        matrix = cv2.getPerspectiveTransform(ordered, target)
        corrected = cv2.warpPerspective(array, matrix, (target_width, target_height), borderValue=(255, 255, 255))
        perspective_corrected = True
        diagnostics.append("Se corrigió la perspectiva usando el contorno principal de la página.")
        break

    corrected_gray = cv2.cvtColor(corrected, cv2.COLOR_RGB2GRAY)
    inverted = cv2.threshold(corrected_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coordinates = np.column_stack(np.where(inverted > 0))
    deskew_degrees = 0.0
    if len(coordinates) > 100:
        angle = float(cv2.minAreaRect(coordinates[:, ::-1].astype("float32"))[-1])
        angle = -(90 + angle) if angle < -45 else -angle
        if 0.15 <= abs(angle) <= 12:
            height, width = corrected.shape[:2]
            rotation = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
            corrected = cv2.warpAffine(
                corrected, rotation, (width, height), flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255),
            )
            deskew_degrees = angle
            diagnostics.append(f"Deskew aplicado: {angle:.2f} grados.")
    return Image.fromarray(corrected).convert("RGB"), deskew_degrees, perspective_corrected, diagnostics


def _opencv_variants(image: Image.Image) -> dict[str, Image.Image] | None:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    background = cv2.medianBlur(gray, 31)
    shadow = cv2.divide(gray, background, scale=255)
    adaptive = cv2.adaptiveThreshold(
        shadow, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11
    )
    adaptive = cv2.medianBlur(adaptive, 3)
    # Variante adicional: nunca reemplaza el original porque la escritura también puede ser azul.
    blue_reduced = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[:, :, 0]
    return {
        "grayscale": Image.fromarray(gray),
        "high_contrast": Image.fromarray(clahe).filter(
            ImageFilter.UnsharpMask(radius=1.0, percent=115, threshold=3)
        ),
        "adaptive_threshold": Image.fromarray(adaptive),
        "shadow_normalized": Image.fromarray(shadow),
        "blue_reduced": Image.fromarray(blue_reduced),
    }


def prepare_image_variants(
    source_path: str | Path,
    output_directory: str | Path,
    *,
    orientation_detector: Callable[[Image.Image], int | None] | None = None,
) -> ImageVariantSet:
    """Genera variantes OCR locales sin sobrescribir ni recodificar el original."""
    original_path = Path(source_path)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    image = load_oriented_rgb(original_path)
    diagnostics: list[str] = ["Orientación EXIF aplicada."]
    rotation = 0
    if orientation_detector is not None:
        try:
            detected = orientation_detector(image)
            if detected in {90, 180, 270}:
                rotation = int(detected)
                image = image.rotate(-rotation, expand=True, fillcolor="white")
                diagnostics.append(f"Rotación de texto corregida: {rotation} grados.")
        except Exception as exc:
            diagnostics.append(f"No se pudo estimar la rotación de texto: {exc}")

    oriented_path = output / "oriented.png"
    image.save(oriented_path, format="PNG", optimize=True)
    image, deskew, perspective_corrected, geometry_diagnostics = _opencv_geometry(image)
    diagnostics.extend(geometry_diagnostics)
    perspective_path = output / "perspective_corrected.png"
    image.save(perspective_path, format="PNG", optimize=True)
    image = upscale_small_document(image)
    variants = _opencv_variants(image)
    if variants is None:
        grayscale = to_grayscale(image)
        variants = {
            "grayscale": grayscale,
            "high_contrast": improve_contrast_and_sharpness(grayscale),
            "adaptive_threshold": _pillow_adaptive_like(grayscale),
            "shadow_normalized": _pillow_shadow_normalization(grayscale),
            "blue_reduced": ImageOps.autocontrast(image.getchannel("B"), cutoff=1),
        }
    all_images = {"original": image, **variants}
    paths: dict[str, Path] = {}
    for name, variant in all_images.items():
        destination = output / f"{name}.png"
        variant.save(destination, format="PNG", optimize=True)
        paths[name] = destination
    return ImageVariantSet(
        original_path=original_path,
        variants=paths,
        width=image.width,
        height=image.height,
        oriented_path=oriented_path,
        perspective_path=perspective_path,
        rotation_degrees=rotation,
        deskew_degrees=deskew,
        perspective_corrected=perspective_corrected,
        diagnostics=diagnostics,
    )


def preprocess_image(source_path: str | Path, destination_path: str | Path) -> Path:
    """Compatibilidad: genera una sola copia PNG mejorada y conserva el original."""
    oriented = load_oriented_rgb(source_path)
    grayscale = to_grayscale(oriented)
    enhanced = improve_contrast_and_sharpness(grayscale)
    prepared = upscale_small_document(enhanced)
    destination = Path(destination_path)
    prepared.save(destination, format="PNG", optimize=True)
    return destination
