from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from PIL import Image


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _device(torch: Any) -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _generate(
    torch: Any,
    processor: Any,
    model: Any,
    image: Image.Image,
    device: str,
) -> tuple[str, float, int]:
    pixels = processor(images=image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
    with torch.inference_mode():
        generated = model.generate(
            pixels,
            max_new_tokens=32,
            return_dict_in_generate=True,
            output_scores=True,
        )
    text = processor.batch_decode(generated.sequences, skip_special_tokens=True)[0].strip()
    confidence = 0.0
    token_count = len(generated.scores)
    if generated.scores:
        transition = model.compute_transition_scores(
            generated.sequences,
            generated.scores,
            normalize_logits=True,
        )
        finite = transition[torch.isfinite(transition)]
        if finite.numel():
            confidence = float(torch.exp(finite.mean()).detach().cpu().item())
    return text, max(0.0, min(1.0, confidence)), token_count


def run(model_path: Path) -> None:
    # Estas variables también se imponen desde el padre: defensa en profundidad contra red accidental.
    os.environ.update({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TOKENIZERS_PARALLELISM": "false",
    })
    started = time.perf_counter()
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    processor = TrOCRProcessor.from_pretrained(str(model_path), local_files_only=True)
    model = VisionEncoderDecoderModel.from_pretrained(str(model_path), local_files_only=True)
    device = _device(torch)
    model.to(device)
    model.eval()
    load_seconds = time.perf_counter() - started
    _emit({"ready": True, "device": device, "model_load_seconds": round(load_seconds, 4)})

    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                return
            raw = base64.b64decode(request["image_base64"], validate=True)
            field_type = str(request.get("field_type") or "")
            if field_type not in {"date", "mileage"}:
                raise ValueError("field_type debe ser date o mileage.")
            with Image.open(io.BytesIO(raw)) as image:
                started = time.perf_counter()
                try:
                    text, confidence, token_count = _generate(torch, processor, model, image, device)
                except RuntimeError:
                    if device == "cpu":
                        raise
                    # Algunas operaciones pueden no estar implementadas por el acelerador disponible.
                    device = "cpu"
                    model.to(device)
                    text, confidence, token_count = _generate(torch, processor, model, image, device)
                elapsed = time.perf_counter() - started
            _emit({
                "text": text,
                "confidence": round(confidence, 6),
                "token_count": token_count,
                "device": device,
                "model_load_seconds": round(load_seconds, 4),
                "inference_seconds": round(elapsed, 4),
            })
        except Exception as exc:
            _emit({"error": f"{type(exc).__name__}: {exc}"})


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Worker JSONL local para TrOCR.")
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args(argv)
    model_path = args.model.expanduser().resolve()
    if not model_path.is_dir():
        _emit({"error": f"No existe el modelo local: {model_path}"})
        return
    try:
        run(model_path)
    except Exception as exc:
        _emit({"error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
