from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import TRAINING_DATA_DIR


def load_confirmed_annotations(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        sample_id = str(record.get("sample_id") or "")
        if not record.get("human_confirmed") or not record.get("verified_value") or not sample_id or sample_id in seen:
            continue
        image = TRAINING_DATA_DIR / str(record.get("image", ""))
        if not image.is_file():
            continue
        seen.add(sample_id)
        records.append(record)
    return records


def split_by_page(
    records: list[dict[str, Any]], *, seed: int = 20260916
) -> dict[str, list[dict[str, Any]]]:
    """Un grupo documento+página nunca se reparte entre train/validation/test."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        group = str(record.get("page_group") or f"document:{record.get('source_document')}:page:{record.get('source_page', 1)}")
        groups[group].append(record)
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    count = len(keys)
    if count >= 3:
        validation_count = max(1, round(count * 0.10))
        test_count = max(1, round(count * 0.10))
    else:
        validation_count = 0
        test_count = 0
    if validation_count + test_count >= count:
        validation_count = 1 if count >= 3 else 0
        test_count = 1 if count >= 3 else 0
    test_keys = set(keys[:test_count])
    validation_keys = set(keys[test_count:test_count + validation_count])
    result = {"train": [], "validation": [], "test": []}
    for key in keys:
        split = "test" if key in test_keys else "validation" if key in validation_keys else "train"
        result[split].extend(groups[key])
    return result


def export_dataset(
    *, annotations: Path, output: Path, seed: int = 20260916
) -> dict[str, int]:
    records = load_confirmed_annotations(annotations)
    splits = split_by_page(records, seed=seed)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "seed": seed,
        "confirmed_only": True,
        "grouping": "source_document+source_page",
        "counts": {name: len(items) for name, items in splits.items()},
    }
    for split, items in splits.items():
        destination = output / f"{split}.jsonl"
        with destination.open("w", encoding="utf-8") as stream:
            for record in items:
                exported = {
                    "image": str((TRAINING_DATA_DIR / record["image"]).resolve()),
                    "text": str(record["verified_value"]),
                    "field": record["field"],
                    "sample_id": record["sample_id"],
                    "page_group": record.get("page_group"),
                }
                stream.write(json.dumps(exported, ensure_ascii=False, separators=(",", ":")) + "\n")
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest["counts"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta sólo recortes confirmados por personas.")
    parser.add_argument("--annotations", type=Path, default=TRAINING_DATA_DIR / "annotations" / "verified.jsonl")
    parser.add_argument("--output", type=Path, default=TRAINING_DATA_DIR / "exports" / "latest")
    parser.add_argument("--seed", type=int, default=20260916)
    args = parser.parse_args()
    counts = export_dataset(annotations=args.annotations, output=args.output, seed=args.seed)
    print(json.dumps({"output": str(args.output), "counts": counts, "seed": args.seed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
