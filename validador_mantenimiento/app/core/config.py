from __future__ import annotations

import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
STORAGE_DIR = PROJECT_DIR / "storage" / "documents"
EVIDENCE_CROP_DIR = PROJECT_DIR / "storage" / "evidence_crops"
TRAINING_DATA_DIR = PROJECT_DIR / "training_data"
LOCAL_MODELS_DIR = PROJECT_DIR / "models"
FRONTEND_DIR = PROJECT_DIR / "frontend"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'validator.db'}")
OCR_PROVIDER = os.getenv("OCR_PROVIDER", "tesseract").strip().lower()
OCR_LANG = os.getenv("OCR_LANG", "spa+eng").strip()
TESSERACT_CMD = os.getenv("TESSERACT_CMD") or None
OCR_REVIEW_THRESHOLD = float(os.getenv("OCR_REVIEW_THRESHOLD", "0.88"))
OCR_MAX_FILE_SIZE_MB = int(os.getenv("OCR_MAX_FILE_SIZE_MB", "10"))
OCR_MAX_IMAGES_PER_ANALYSIS = int(os.getenv("OCR_MAX_IMAGES_PER_ANALYSIS", "15"))
OCR_MAX_IMAGE_PIXELS = int(os.getenv("OCR_MAX_IMAGE_PIXELS", "40000000"))
HANDWRITING_MODEL_PATH = os.getenv("HANDWRITING_MODEL_PATH") or None
HANDWRITING_PYTHON = os.getenv("HANDWRITING_PYTHON") or None
HANDWRITING_TIMEOUT_SECONDS = float(os.getenv("HANDWRITING_TIMEOUT_SECONDS", "180"))


def _environment_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


OCR_DEBUG = _environment_flag("OCR_DEBUG")
_debug_directory = Path(os.getenv("OCR_DEBUG_DIR", str(PROJECT_DIR / "debug_ocr"))).expanduser()
OCR_DEBUG_DIR = _debug_directory if _debug_directory.is_absolute() else PROJECT_DIR / _debug_directory

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/octet-stream",  # Algunos navegadores no identifican correctamente el MIME.
}


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_CROP_DIR.mkdir(parents=True, exist_ok=True)
    (TRAINING_DATA_DIR / "crops" / "dates").mkdir(parents=True, exist_ok=True)
    (TRAINING_DATA_DIR / "crops" / "mileage").mkdir(parents=True, exist_ok=True)
    (TRAINING_DATA_DIR / "annotations").mkdir(parents=True, exist_ok=True)
    LOCAL_MODELS_DIR.mkdir(parents=True, exist_ok=True)
