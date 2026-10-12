from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_ID = "microsoft/trocr-base-handwritten"
DEFAULT_OUTPUT = PROJECT_DIR / "models" / "pretrained" / "trocr-base-handwritten"


def download_model(model_id: str, output: Path) -> dict[str, object]:
    """Realiza la única fase online y deja un paquete autocontenido para inferencia offline."""
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            f"El destino ya contiene archivos: {output}. No se sobrescribió nada."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{output.name}.staging")
    if staging.exists():
        raise ValueError(f"Existe una descarga incompleta: {staging}. Revísala antes de continuar.")

    try:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    except ImportError as exc:
        raise RuntimeError("Instala requirements-ocr-ml.txt en el entorno aislado.") from exc

    try:
        with TemporaryDirectory(prefix="trocr-hf-cache-") as cache:
            processor = TrOCRProcessor.from_pretrained(model_id, cache_dir=cache)
            model = VisionEncoderDecoderModel.from_pretrained(
                model_id,
                cache_dir=cache,
                use_safetensors=True,
            )
            processor.save_pretrained(staging)
            model.save_pretrained(staging, safe_serialization=True)
            commit = getattr(model.config, "_commit_hash", None)
        manifest = {
            "source_model": model_id,
            "source_revision": commit,
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
            "license": "MIT",
            "fine_tuned_locally": False,
            "runtime_network_required": False,
        }
        (staging / "local-model-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        staging.replace(output)
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Descarga una vez TrOCR y lo guarda para inferencia estrictamente local."
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        manifest = download_model(args.model_id, args.output.expanduser().resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output.resolve()), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
