"""Instala el adaptador de Vision de macOS; no descarga modelos ni dependencias."""
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


def main():
    root = Path(__file__).resolve().parents[1]
    compiler = shutil.which("swiftc")
    if sys.platform != "darwin" or not compiler:
        raise SystemExit("Requiere macOS y las herramientas de desarrollo de Apple (swiftc).")
    destination = root / "models" / "native" / "vision-ocr"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="maintenance-vision-build-") as cache:
        temporary = destination.with_suffix(".new")
        subprocess.run([compiler, "-O", "-module-cache-path", cache,
                        str(root / "app/services/native_vision_ocr.swift"), "-o", str(temporary)],
                       check=True, timeout=600)
        temporary.replace(destination)
    print(f"OCR local instalado: {destination}")


if __name__ == "__main__":
    main()
