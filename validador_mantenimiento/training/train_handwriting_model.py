from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from PIL import Image, ImageEnhance

from app.core.config import LOCAL_MODELS_DIR


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"No existe el split requerido: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _next_version(root: Path) -> Path:
    versions = []
    for candidate in root.glob("maintenance_handwriting_v*"):
        try:
            versions.append(int(candidate.name.rsplit("v", 1)[1]))
        except ValueError:
            pass
    return root / f"maintenance_handwriting_v{max(versions, default=0) + 1}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tuning offline de TrOCR sobre crops confirmados.")
    parser.add_argument("--dataset", type=Path, default=Path("training_data/exports/latest"))
    parser.add_argument("--base-model", type=Path, required=True, help="Directorio local del checkpoint base.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260916)
    args = parser.parse_args()
    if not args.base_model.is_dir():
        raise SystemExit("--base-model debe ser un checkpoint local; el script no descarga modelos automáticamente.")
    try:
        import torch
        from torch.utils.data import Dataset
        from transformers import Seq2SeqTrainer, Seq2SeqTrainingArguments, TrOCRProcessor, VisionEncoderDecoderModel
    except ImportError as exc:
        raise SystemExit("Instala requirements-ocr-ml.txt en un entorno OCR aislado.") from exc

    random.seed(args.seed)
    processor = TrOCRProcessor.from_pretrained(str(args.base_model), local_files_only=True)
    model = VisionEncoderDecoderModel.from_pretrained(str(args.base_model), local_files_only=True)

    class CropDataset(Dataset):
        def __init__(self, rows: list[dict], augment: bool) -> None:
            self.rows = rows
            self.augment = augment

        def __len__(self) -> int:
            return len(self.rows)

        def __getitem__(self, index: int):
            row = self.rows[index]
            with Image.open(row["image"]) as source:
                image = source.convert("RGB")
            if self.augment:
                image = image.rotate(random.uniform(-2.0, 2.0), expand=False, fillcolor="white")
                image = ImageEnhance.Contrast(image).enhance(random.uniform(0.90, 1.12))
            pixels = processor(images=image, return_tensors="pt").pixel_values.squeeze(0)
            labels = processor.tokenizer(
                str(row["text"]), max_length=32, padding="max_length", truncation=True, return_tensors="pt"
            ).input_ids.squeeze(0)
            labels[labels == processor.tokenizer.pad_token_id] = -100
            return {"pixel_values": pixels, "labels": labels}

    train_rows = _read_jsonl(args.dataset / "train.jsonl")
    validation_rows = _read_jsonl(args.dataset / "validation.jsonl")
    if not train_rows:
        raise SystemExit("El split train no contiene muestras confirmadas.")
    output = args.output or _next_version(LOCAL_MODELS_DIR)
    if output.exists():
        raise SystemExit(f"El directorio de salida ya existe: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    training_args = Seq2SeqTrainingArguments(
        output_dir=str(output),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        learning_rate=5e-5,
        predict_with_generate=True,
        eval_strategy="epoch" if validation_rows else "no",
        save_strategy="epoch",
        logging_steps=10,
        seed=args.seed,
        report_to=[],
        load_best_model_at_end=bool(validation_rows),
        metric_for_best_model="eval_loss" if validation_rows else None,
        greater_is_better=False,
    )
    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=CropDataset(train_rows, augment=True),
        eval_dataset=CropDataset(validation_rows, augment=False) if validation_rows else None,
        processing_class=processor,
    )
    trainer.train()
    trainer.save_model(str(output))
    processor.save_pretrained(str(output))
    (output / "training_manifest.json").write_text(json.dumps({
        "base_model": str(args.base_model.resolve()),
        "dataset": str(args.dataset.resolve()),
        "seed": args.seed,
        "epochs": args.epochs,
        "promoted": False,
        "activation": "Configura HANDWRITING_MODEL_PATH explícitamente después de evaluar.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Modelo guardado en {output}. No se cambió el modelo activo.")


if __name__ == "__main__":
    main()
