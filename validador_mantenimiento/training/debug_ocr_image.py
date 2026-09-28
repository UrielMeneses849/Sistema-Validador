from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence

from app.core.config import OCR_DEBUG_DIR, OCR_LANG
from app.services.extraction_service import OcrExtractor, OcrProcessingError, OcrUnavailableError


def _boxes_from_diagnostics(messages: list[str]) -> int:
    for message in reversed(messages):
        match = re.search(r"(\d+)\s+cuadros?\s+candidatos?", message, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Ejecuta el lector local y conserva todas las imágenes/candidatos de diagnóstico."
    )
    parser.add_argument("image", type=Path)
    parser.add_argument("--run-id", help="Nombre de la carpeta; usa el document_id cuando exista.")
    parser.add_argument("--output-dir", type=Path, default=OCR_DEBUG_DIR)
    parser.add_argument("--language", default=OCR_LANG)
    parser.add_argument("--tesseract-cmd")
    args = parser.parse_args(argv)
    image = args.image.expanduser().resolve()
    if not image.is_file():
        parser.error(f"No existe la imagen: {image}")
    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(image.suffix.lower(), "application/octet-stream")
    try:
        extraction = OcrExtractor(
            language=args.language,
            tesseract_cmd=args.tesseract_cmd,
            debug_enabled=True,
            debug_directory=args.output_dir,
            debug_run_id=args.run_id or image.stem,
        ).extract(str(image), mime_type)
    except (OcrUnavailableError, OcrProcessingError) as exc:
        parser.error(str(exc))

    metadata = extraction.metadata
    print(json.dumps({
        "image": str(image),
        "debug_directory": metadata.get("debug_directory"),
        "boxes_detected": _boxes_from_diagnostics(metadata.get("diagnostics", [])),
        "detected_events": metadata.get("event_count", 0),
        "events": metadata.get("maintenance_image_events", []),
        "warnings": extraction.warnings,
        "diagnostics": metadata.get("diagnostics", []),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
