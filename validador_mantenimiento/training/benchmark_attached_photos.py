"""Comparación local reproducible; nunca modifica originales ni la base de datos."""
from pathlib import Path
import argparse
import json
import time
import pytesseract
from app.services.maintenance_image_recognizer import LocalMaintenanceImageRecognizer, TesseractFieldOcrBackend


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, default=Path('debug_ocr/photos-benchmark'))
    parser.add_argument('--files', nargs='+', default=[], help='Nombres explícitos de las fotos autorizadas.')
    parser.add_argument('--probe-crops', type=Path)
    args = parser.parse_args()
    if args.probe_crops:
        from PIL import Image
        from app.services.native_vision_backend import NativeVisionBackend
        from app.services.image_preprocessing import field_reading_variants
        backend = NativeVisionBackend()
        for path in sorted(args.probe_crops.glob('service-*/*/original.png')):
            readings = {name: backend.read(image) for name, image in field_reading_variants(Image.open(path)).items()}
            print(path.relative_to(args.probe_crops), json.dumps(readings,ensure_ascii=False),flush=True)
        backend.close()
        return
    if not args.files:
        parser.error('Indica --files para limitar la prueba a las fotos autorizadas.')
    rows = []
    for name in args.files:
        source = args.directory / name
        if source.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
            continue
        started = time.monotonic()
        try:
            recognizer = LocalMaintenanceImageRecognizer(TesseractFieldOcrBackend(pytesseract),
                evidence_directory=args.output / 'evidence', debug_enabled=True,
                debug_directory=args.output, debug_run_id=source.stem)
            result = recognizer.analyze(source)
            row = {'file': source.name, 'seconds': round(time.monotonic()-started, 1),
                   'diagnostics': result.diagnostics, 'events': result.metadata_events()}
        except Exception as exc:
            row = {'file': source.name, 'error': f'{type(exc).__name__}: {exc}'}
        rows.append(row)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'predictions.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        print(json.dumps({**row, 'events': [(e['region_index'],e['service_date']['normalized_value'],e['mileage']['normalized_value']) for e in row.get('events',[])]},ensure_ascii=False),flush=True)

if __name__ == '__main__':
    main()
