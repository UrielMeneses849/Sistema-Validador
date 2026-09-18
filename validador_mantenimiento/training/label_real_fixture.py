from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Sequence

from training.real_benchmark import (
    DEFAULT_GROUND_TRUTH,
    GroundTruthEvent,
    normalize_ground_truth_event,
    save_ground_truth,
)


def collect_events(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> list[GroundTruthEvent]:
    output_fn("Captura los mantenimientos en orden visual (de arriba hacia abajo).")
    output_fn("Deja service_date vacío cuando termines.")
    events: list[GroundTruthEvent] = []
    while True:
        raw_date = input_fn(f"Evento {len(events) + 1} - service_date [YYYY-MM-DD]: ").strip()
        if not raw_date:
            if events:
                return events
            output_fn("Debes registrar al menos un evento.")
            continue
        raw_mileage = input_fn(f"Evento {len(events) + 1} - mileage_km: ").strip()
        try:
            events.append(normalize_ground_truth_event(raw_date, raw_mileage))
        except ValueError as exc:
            output_fn(f"Valor inválido: {exc}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Etiqueta manualmente los eventos de una fotografía para el benchmark real."
    )
    parser.add_argument("image", type=Path, help="Fotografía real que se usará como fixture.")
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=DEFAULT_GROUND_TRUTH,
        help="JSONL de benchmark; nunca se mezcla con training_data.",
    )
    parser.add_argument(
        "--event",
        action="append",
        nargs=2,
        metavar=("SERVICE_DATE", "MILEAGE_KM"),
        help="Evento no interactivo; se puede repetir y debe respetar el orden visual.",
    )
    args = parser.parse_args(argv)

    try:
        events = (
            [normalize_ground_truth_event(service_date, mileage) for service_date, mileage in args.event]
            if args.event
            else collect_events()
        )
        record, replaced = save_ground_truth(
            args.image,
            events,
            ground_truth_path=args.ground_truth,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({
        "ground_truth": str(args.ground_truth.resolve()),
        "fixture_id": record["fixture_id"],
        "events": len(record["events"]),
        "replaced": replaced,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
