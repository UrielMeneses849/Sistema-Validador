from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from training.real_benchmark import (
    DEFAULT_GROUND_TRUTH,
    evaluate_real_benchmark,
    load_ground_truth,
    load_predictions,
    run_local_predictions,
    write_jsonl,
)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Evalúa el pipeline local contra fotografías reales etiquetadas."
    )
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument(
        "--predictions",
        type=Path,
        help="JSON/JSONL precomputado. Si se omite, ejecuta el OCR local sobre todas las fotografías.",
    )
    parser.add_argument("--save-predictions", type=Path, help="Guarda las predicciones de esta ejecución como JSONL.")
    parser.add_argument("--output", type=Path, help="Guarda el reporte completo como JSON.")
    parser.add_argument("--threshold", type=float, default=0.88, help="Umbral usado si una predicción no trae revisión explícita.")
    args = parser.parse_args(argv)
    if not 0.0 <= args.threshold <= 1.0:
        parser.error("--threshold debe estar entre 0 y 1.")

    try:
        fixtures = load_ground_truth(args.ground_truth)
        predictions = load_predictions(args.predictions) if args.predictions else run_local_predictions(fixtures)
        if args.save_predictions:
            write_jsonl(args.save_predictions, predictions)
        report = evaluate_real_benchmark(fixtures, predictions, threshold=args.threshold)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
