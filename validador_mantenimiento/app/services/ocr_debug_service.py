from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from app.services.image_preprocessing import ImageVariantSet


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")[:100] or "image"


def _box_payload(box: Any) -> dict[str, int]:
    return {
        "x0": int(box.x0),
        "y0": int(box.y0),
        "x1": int(box.x1),
        "y1": int(box.y1),
        "width": int(box.width),
        "height": int(box.height),
    }


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class OcrDebugSession:
    """Persistencia local de diagnóstico; no crea archivos cuando está desactivada."""

    def __init__(
        self,
        *,
        enabled: bool,
        base_directory: str | Path,
        run_id: str,
    ) -> None:
        self.enabled = enabled
        self.directory = Path(base_directory) / _safe_name(run_id)

    @property
    def public_path(self) -> str | None:
        return str(self.directory.resolve()) if self.enabled else None

    def initialize(self, source: Path, variants: ImageVariantSet) -> None:
        if not self.enabled:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        copies = {
            "original-oriented.png": variants.oriented_path,
            "perspective-corrected.png": variants.perspective_path,
        }
        for filename, source_path in copies.items():
            if source_path and source_path.is_file():
                shutil.copy2(source_path, self.directory / filename)

        page_variants = self.directory / "page-variants"
        page_variants.mkdir(parents=True, exist_ok=True)
        for name, source_path in variants.variants.items():
            shutil.copy2(source_path, page_variants / f"{_safe_name(name)}.png")

        self.write_json("preprocessing.json", {
            "source": str(source.resolve()),
            "source_name": source.name,
            "width": variants.width,
            "height": variants.height,
            "rotation_degrees": variants.rotation_degrees,
            "deskew_degrees": variants.deskew_degrees,
            "perspective_corrected": variants.perspective_corrected,
            "diagnostics": variants.diagnostics,
            "page_variants": sorted(variants.variants),
        })

    def capture_layout(
        self,
        variants: ImageVariantSet,
        *,
        tokens: Iterable[Any],
        rectangles: Iterable[Any],
        regions: Iterable[Any],
        layout_variants: dict[str, list[Any]],
    ) -> None:
        if not self.enabled:
            return
        token_list = list(tokens)
        rectangle_list = list(rectangles)
        region_list = list(regions)
        with Image.open(variants.variants["original"]) as source:
            overlay = source.convert("RGB")
        draw = ImageDraw.Draw(overlay)
        font = ImageFont.load_default()

        for rectangle in rectangle_list:
            draw.rectangle(
                (rectangle.x0, rectangle.y0, rectangle.x1, rectangle.y1),
                outline=(145, 145, 145),
                width=2,
            )
        for token in token_list:
            if getattr(token, "label_type", None):
                color = (125, 60, 190) if token.label_type == "date" else (175, 85, 15)
                draw.rectangle((token.box.x0, token.box.y0, token.box.x1, token.box.y1), outline=color, width=3)
        for index, region in enumerate(region_list, start=1):
            draw.rectangle((region.box.x0, region.box.y0, region.box.x1, region.box.y1), outline=(30, 150, 60), width=4)
            draw.rectangle(
                (region.date_crop.x0, region.date_crop.y0, region.date_crop.x1, region.date_crop.y1),
                outline=(20, 90, 230), width=4,
            )
            draw.rectangle(
                (region.mileage_crop.x0, region.mileage_crop.y0, region.mileage_crop.x1, region.mileage_crop.y1),
                outline=(235, 125, 20), width=4,
            )
            draw.rectangle((region.box.x0, region.box.y0, region.box.x0 + 82, region.box.y0 + 19), fill=(30, 150, 60))
            draw.text((region.box.x0 + 3, region.box.y0 + 3), f"service {index}", fill="white", font=font)
        overlay.save(self.directory / "bounding-boxes.png", format="PNG", optimize=True)

        self.write_json("layout.json", {
            "legend": {
                "geometric_rectangle": "gray",
                "detected_label": "purple_or_brown",
                "service_box": "green",
                "date_crop": "blue",
                "mileage_crop": "orange",
            },
            "tokens": [
                {
                    "text": token.text,
                    "confidence": token.confidence,
                    "label_type": getattr(token, "label_type", None),
                    "box": _box_payload(token.box),
                }
                for token in token_list
            ],
            "layout_variants": {
                name: [
                    {"text": token.text, "confidence": token.confidence, "box": _box_payload(token.box)}
                    for token in variant_tokens
                ]
                for name, variant_tokens in layout_variants.items()
            },
            "geometric_rectangles": [_box_payload(box) for box in rectangle_list],
            "service_regions": [
                {
                    "index": index,
                    "strategy": region.strategy,
                    "date_label": region.date_label,
                    "mileage_label": region.mileage_label,
                    "service_box": _box_payload(region.box),
                    "date_crop": _box_payload(region.date_crop),
                    "mileage_crop": _box_payload(region.mileage_crop),
                }
                for index, region in enumerate(region_list, start=1)
            ],
        })

    def capture_region(self, variants: ImageVariantSet, *, index: int, region: Any) -> None:
        if not self.enabled:
            return
        destination = self.directory / f"service-{index:03d}"
        destination.mkdir(parents=True, exist_ok=True)
        with Image.open(variants.variants["original"]) as image:
            image.crop((region.box.x0, region.box.y0, region.box.x1, region.box.y1)).save(
                destination / "service-box.png", format="PNG", optimize=True
            )
        for field_name, crop in (("date", region.date_crop), ("mileage", region.mileage_crop)):
            field_directory = destination / field_name
            field_directory.mkdir(parents=True, exist_ok=True)
            for variant_name, path in variants.variants.items():
                with Image.open(path) as image:
                    image.crop((crop.x0, crop.y0, crop.x1, crop.y1)).save(
                        field_directory / f"{_safe_name(variant_name)}.png",
                        format="PNG",
                        optimize=True,
                    )

    def write_report(self, payload: dict[str, Any]) -> None:
        if self.enabled:
            self.write_json("ocr-report.json", payload)

    def write_json(self, filename: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        destination = self.directory / filename
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)


def box_payload(box: Any) -> dict[str, int]:
    return _box_payload(box)
