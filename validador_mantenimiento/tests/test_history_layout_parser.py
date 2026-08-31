from __future__ import annotations

from app.services.document_parser import parse_document
from app.services.extraction_service import ExtractedPage, ExtractionResult, LayoutWord


def word(text: str, page: int, x0: float, top: float) -> LayoutWord:
    return LayoutWord(text=text, page_number=page, x0=x0, x1=x0 + max(12, len(text) * 4), top=top, bottom=top + 10)


def history_extraction() -> ExtractionResult:
    """Fixture de layout: los valores están en columnas, no en texto lineal."""
    words: list[LayoutWord] = []

    def header(page: int) -> None:
        words.extend(
            [
                word("Fecha", page, 3, 80),
                word("Orden", page, 45, 80),
                word("Asesor", page, 76, 80),
                word("Kms", page, 413, 80),
                word("Mecánico", page, 431, 80),
                word("Trabajo", page, 565, 80),
            ]
        )

    def record(
        page: int,
        top: float,
        service_date: str,
        mileage: str,
        work: str,
        *,
        advisor: str = "1343",
        mechanic_context: str = "",
    ) -> None:
        words.extend(
            [
                word(service_date, page, 3, top),
                word("220665", page, 45, top),
                word(advisor, page, 76, top),
                word(mileage, page, 405, top),
                word(mechanic_context, page, 434, top) if mechanic_context else word("Mec", page, 434, top),
                word(work, page, 584, top),
            ]
        )

    def continuation(page: int, top: float, work: str) -> None:
        words.append(word(work, page, 584, top))

    # Fecha de emisión del reporte: está arriba de la cabecera, no en Fecha/Kms.
    words.append(word("10/06/2026", 1, 3, 40))
    header(1)
    record(1, 100, "29/11/2024", "24,675", "CAMBIO ACEITE Y FILTRO")
    continuation(1, 114, "ROTACION")
    record(1, 140, "07/06/2025", "36,917", "ROTACION")
    continuation(1, 154, "CAMBIO ACEITE Y FILTRO")
    record(1, 180, "11/06/2025", "36,917", "CAMPAÑA A242472980")
    record(1, 210, "30/06/2025", "38,564", "MATERIALES DIVERSOS", mechanic_context="CANCELADA")
    record(1, 240, "30/06/2025", "38,564", "REEMPLAZO MOLDURA")
    record(1, 270, "01/11/2025", "48,274", "CAMBIO ACEITE Y FILTRO")
    continuation(1, 284, "ROTACION")
    continuation(1, 298, "FILTRO AIRE")
    continuation(1, 312, "FILTRO COMBUSTIBLE")
    continuation(1, 326, "BUJIA")
    record(1, 350, "22/01/2026", "54,182", "SERVICIO FRENOS")
    continuation(1, 364, "TORNO DISCOS")
    continuation(1, 378, "AIRE ACONDICIONADO")
    record(1, 410, "07/04/2026", "59,670", "CAMBIO ACEITE Y FILTRO")
    continuation(1, 424, "ROTACION")
    continuation(1, 438, "SERVICIO TRANSMISION")
    continuation(1, 452, "ALINEACION Y BALANCEO")
    # La continuación del 07/04 persiste entre páginas; sólo una fecha abre otro evento.
    header(2)
    continuation(2, 100, "MATERIALES DIVERSOS")
    record(2, 130, "09/04/2026", "59,759", "TORNO DE DISCOS")

    return ExtractionResult(
        method="pdf_text",
        pages=[
            ExtractedPage(1, "HISTORIAL DE SERVICIO"),
            ExtractedPage(2, "HISTORIAL DE SERVICIO"),
        ],
        words=words,
        has_usable_text=True,
    )


def test_history_is_reconstructed_by_table_columns_and_not_report_date_or_advisor_id():
    parsed = parse_document(history_extraction())

    assert parsed.document_type == "historial_servicio"
    assert parsed.document_generated_at.normalized_value.isoformat() == "2026-06-10"
    assert len(parsed.service_events) == 9
    assert all(event.mileage.normalized_value != 1343 for event in parsed.service_events)
    assert all(event.service_date.normalized_value.isoformat() != "2026-06-10" for event in parsed.service_events)

    latest_preventive = max(
        (event for event in parsed.service_events if event.resets_maintenance_interval is True),
        key=lambda event: event.service_date.normalized_value,
    )
    assert latest_preventive.service_date.normalized_value.isoformat() == "2026-04-07"
    assert latest_preventive.mileage.normalized_value == 59670
    assert latest_preventive.service_category == "preventive_maintenance"
    assert "CAMBIO ACEITE Y FILTRO" in latest_preventive.works
    assert latest_preventive.work_evidence[0]["coordinates"]["x0"] >= 584

    last_event = parsed.service_events[-1]
    assert last_event.service_date.normalized_value.isoformat() == "2026-04-09"
    assert last_event.mileage.normalized_value == 59759
    assert last_event.service_category == "repair"
    assert last_event.resets_maintenance_interval is False

    assert parsed.layout_debug["strategy"] == "history_table"
    assert parsed.layout_debug["latest_preventive_maintenance"] == {
        "date": "2026-04-07",
        "mileage_km": 59670,
        "reason": "Evento preventivo más reciente por fecha de servicio; se excluyen campaña, cancelación y reparación.",
    }
