from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image

from training.metrics import calculate_metrics


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"No existe el dataset/predicciones: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _predict(dataset: list[dict[str, Any]], model_path: Path) -> list[dict[str, Any]]:
    if not model_path.is_dir():
        raise SystemExit(f"No existe el modelo local: {model_path}")
    try:
        import torch
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    except ImportError as exc:
        raise SystemExit("Instala requirements-ocr-ml.txt en el entorno OCR aislado.") from exc
    processor = TrOCRProcessor.from_pretrained(str(model_path), local_files_only=True)
    model = VisionEncoderDecoderModel.from_pretrained(str(model_path), local_files_only=True)
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval()
    output: list[dict[str, Any]] = []
    for row in dataset:
        with Image.open(row["image"]) as image, torch.inference_mode():
            pixels = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
            generated = model.generate(pixels, max_new_tokens=32, return_dict_in_generate=True, output_scores=True)
        prediction = processor.batch_decode(generated.sequences, skip_special_tokens=True)[0]
        # La confianza generativa aún no está calibrada contra falsas aceptaciones; es conservadora.
        output.append({**row, "prediction": prediction, "confidence": 0.70, "accepted": False})
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Evalúa exactitud de fecha/km y falsas aceptaciones.")
    parser.add_argument("--dataset", type=Path, default=Path("training_data/exports/latest/test.jsonl"))
    parser.add_argument("--model", type=Path)
    parser.add_argument("--predictions", type=Path, help="JSONL ya inferido; evita cargar Torch/TrOCR.")
    parser.add_argument("--threshold", type=float, default=0.88)
    args = parser.parse_args()
    rows = _read_jsonl(args.predictions) if args.predictions else _predict(_read_jsonl(args.dataset), args.model) if args.model else None
    if rows is None:
        raise SystemExit("Indica --model o --predictions.")
    print(json.dumps(calculate_metrics(rows, threshold=args.threshold), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
