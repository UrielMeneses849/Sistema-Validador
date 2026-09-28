from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import TRAINING_DATA_DIR


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa crops fecha/km ya etiquetados; no admite páginas completas sin recortar."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="JSONL con image, field, text y page_group.")
    args = parser.parse_args()
    if not args.manifest.is_file():
        raise SystemExit(f"No existe el manifest: {args.manifest}")
    annotations = TRAINING_DATA_DIR / "annotations" / "verified.jsonl"
    annotations.parent.mkdir(parents=True, exist_ok=True)
    existing = {
        json.loads(line).get("sample_id")
        for line in annotations.read_text(encoding="utf-8").splitlines()
        if line.strip()
    } if annotations.is_file() else set()
    imported = 0
    for number, line in enumerate(args.manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        field = str(row.get("field", ""))
        if field not in {"date", "mileage"}:
            raise SystemExit(f"Línea {number}: field debe ser date o mileage.")
        image = Path(str(row.get("image", ""))).expanduser()
        text = str(row.get("text", "")).strip()
        if not image.is_file() or not text or not row.get("page_group"):
            raise SystemExit(f"Línea {number}: faltan image válido, text o page_group.")
        content_hash = hashlib.sha256(image.read_bytes()).hexdigest()
        sample_id = hashlib.sha256(f"{content_hash}:{field}:{text}".encode()).hexdigest()
        if sample_id in existing:
            continue
        suffix = image.suffix.lower() if image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
        destination = TRAINING_DATA_DIR / "crops" / ("dates" if field == "date" else "mileage") / f"{content_hash}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(image, destination)
        payload = {
            "sample_id": sample_id,
            "image": destination.relative_to(TRAINING_DATA_DIR).as_posix(),
            "field": field,
            "ocr_prediction": row.get("ocr_prediction"),
            "verified_value": text,
            "text": text,
            "source_document": row.get("source_document", "manual-import"),
            "source_page": row.get("source_page", 1),
            "page_group": row["page_group"],
            "confidence": row.get("confidence"),
            "preprocessing_variant": row.get("preprocessing_variant"),
            "human_confirmed": True,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with annotations.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        existing.add(sample_id)
        imported += 1
    print(json.dumps({"imported": imported, "annotations": str(annotations)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
