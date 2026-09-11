from __future__ import annotations

import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
STORAGE_DIR = PROJECT_DIR / "storage" / "documents"
FRONTEND_DIR = PROJECT_DIR / "frontend"
DEFAULT_TESSDATA_DIR = DATA_DIR / "tessdata"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'validator.db'}")
OCR_PROVIDER = os.getenv("OCR_PROVIDER", "tesseract").strip().lower()
OCR_LANG = os.getenv("OCR_LANG", "spa+eng").strip()
TESSERACT_CMD = os.getenv("TESSERACT_CMD") or None
TESSDATA_DIR = Path(os.getenv("TESSDATA_DIR", DEFAULT_TESSDATA_DIR))
OCR_REVIEW_THRESHOLD = float(os.getenv("OCR_REVIEW_THRESHOLD", "0.88"))
OCR_MAX_FILE_SIZE_MB = int(os.getenv("OCR_MAX_FILE_SIZE_MB", "10"))
OCR_MAX_IMAGES_PER_ANALYSIS = int(os.getenv("OCR_MAX_IMAGES_PER_ANALYSIS", "15"))
OCR_MAX_IMAGE_PIXELS = int(os.getenv("OCR_MAX_IMAGE_PIXELS", "40000000"))

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
