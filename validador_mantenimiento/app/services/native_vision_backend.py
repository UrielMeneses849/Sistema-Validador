"""Adaptador opcional del OCR de macOS. No descarga ni envía imágenes."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import select
import subprocess
import sys

from app.core.config import LOCAL_MODELS_DIR


class NativeVisionBackend:
    name = "apple_vision_local"

    def __init__(self):
        self.executable = Path(os.getenv("VISION_OCR_EXECUTABLE", str(LOCAL_MODELS_DIR / "native" / "vision-ocr")))
        self.enabled = os.getenv("VISION_OCR_ENABLED", "1").lower() not in {"0", "false", "off"}
        self.worker = None
        self.failure = None

    @property
    def configured(self):
        return self.enabled and sys.platform == "darwin" and self.executable.is_file() and not self.failure

    def _receive(self):
        if not select.select([self.worker.stdout], [], [], 30)[0]:
            raise RuntimeError("El OCR de macOS excedió 30 segundos.")
        response = json.loads(self.worker.stdout.readline())
        if "error" in response:
            raise RuntimeError(response["error"])
        return response

    def read(self, image):
        if self.worker is None:
            self.worker = subprocess.Popen([str(self.executable)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            if not self._receive().get("ready"):
                raise RuntimeError("El OCR de macOS no está listo.")
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        self.worker.stdin.write(json.dumps({"image_base64": base64.b64encode(buffer.getvalue()).decode("ascii")}) + "\n")
        self.worker.stdin.flush()
        return self._receive()

    def close(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            try:
                worker.stdin.close()
                worker.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                worker.kill()
                worker.wait(timeout=2)
            finally:
                worker.stdout.close()

    def __del__(self):
        self.close()
