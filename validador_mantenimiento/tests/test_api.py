from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import STORAGE_DIR


FRONTEND_VERSION = "intelidrive-dark-3"
MAIN_NAVIGATION = [
    ("/validate_document.html", "Validador de mantenimientos PDF"),
    ("/validate_images.html", "Validador de mantenimientos imágenes"),
    ("/vehicles.html", "Vehículos"),
    ("/manufacturer_rules.html", "Reglas de fabricante"),
    ("/history.html", "Historial"),
]
FRONTEND_PAGES = [
    ("/", "/validate_images.html"),
    ("/validate_images.html", "/validate_images.html"),
    ("/validate_document.html", "/validate_document.html"),
    ("/vehicles.html", "/vehicles.html"),
    ("/manufacturer_rules.html", "/manufacturer_rules.html"),
    ("/register_maintenance.html", "/vehicles.html"),
    ("/history.html", "/history.html"),
    ("/results.html", "/history.html"),
    ("/dashboard.html", None),
]


class FrontendPageParser(HTMLParser):
    """Inspect the shell actually served, including nested SVG navigation icons."""

    def __init__(self):
        super().__init__()
        self.navigation = []
        self.page_links = []
        self.stylesheets = []
        self.scripts = []
        self._in_navigation = False
        self._navigation_link = None

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "nav" and attributes.get("aria-label") == "Navegación principal":
            self._in_navigation = True
        if tag == "a":
            href = attributes.get("href", "")
            if href.startswith("/") and urlsplit(href).path.endswith(".html"):
                self.page_links.append(href)
            if self._in_navigation:
                self._navigation_link = {
                    "href": href,
                    "text": [],
                    "active": "active" in attributes.get("class", "").split(),
                    "current": attributes.get("aria-current"),
                }
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.stylesheets.append(attributes.get("href", ""))
        if tag == "script" and attributes.get("src"):
            self.scripts.append(attributes["src"])

    def handle_data(self, data):
        if self._navigation_link is not None:
            self._navigation_link["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._navigation_link is not None:
            link = self._navigation_link
            link["text"] = " ".join("".join(link["text"]).split())
            self.navigation.append(link)
            self._navigation_link = None
        if tag == "nav":
            self._in_navigation = False


@pytest.mark.parametrize("route,active_page", FRONTEND_PAGES)
def test_every_frontend_page_preserves_the_simple_dark_navigation(
    client: TestClient, route: str, active_page: str | None
):
    response = client.get(route)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    conditional = client.get(route, headers={"if-none-match": response.headers["etag"]})
    assert conditional.status_code == 200
    assert conditional.headers["cache-control"] == "no-store"
    assert conditional.text == response.text
    page = FrontendPageParser()
    page.feed(response.text)
    assert [
        (urlsplit(link["href"]).path, link["text"]) for link in page.navigation
    ] == MAIN_NAVIGATION
    assert [urlsplit(href).path for href in page.stylesheets] == [
        "/css/styles.css",
        "/css/intelidrive-dark.css",
    ]
    active_links = [link for link in page.navigation if link["active"]]
    if active_page is None:
        assert active_links == []
    else:
        assert [urlsplit(link["href"]).path for link in active_links] == [active_page]
    assert [link for link in page.navigation if link["current"] == "page"] == active_links

    # New HTML must bypass already cached destinations and load a coherent asset set.
    assert page.page_links
    assert page.scripts
    for href in page.page_links + page.stylesheets + page.scripts:
        assert parse_qs(urlsplit(href).query).get("v") == [FRONTEND_VERSION], href
    for href in sorted(set(page.page_links)):
        destination = client.get(href)
        base_destination = client.get(urlsplit(href).path)
        assert destination.status_code == base_destination.status_code == 200
        assert destination.headers["cache-control"] == "no-store"
        assert destination.text == base_destination.text


@pytest.mark.parametrize(
    "asset",
    [
        "/css/styles.css",
        f"/css/intelidrive-dark.css?v={FRONTEND_VERSION}",
        "/js/api.js",
        f"/js/vehicles.js?v={FRONTEND_VERSION}",
    ],
)
def test_frontend_assets_revalidate_including_conditional_responses(
    client: TestClient, asset: str
):
    response = client.get(asset)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    for request_header, response_header in [
        ("if-none-match", "etag"),
        ("if-modified-since", "last-modified"),
    ]:
        conditional = client.get(
            asset, headers={request_header: response.headers[response_header]}
        )
        assert conditional.status_code == 304
        assert conditional.headers["cache-control"] == "no-cache"
        assert conditional.content == b""


def test_frontend_routes_keep_image_validator_and_dashboard_available(client: TestClient):
    audit = client.get("/")
    images = client.get("/validate_images.html")
    dashboard = client.get("/dashboard.html")
    manufacturer_rules = client.get("/manufacturer_rules.html")

    assert audit.status_code == 200
    assert "Validador de mantenimientos imágenes" in audit.text
    assert images.status_code == 200
    assert images.text == audit.text
    assert 'id="audit-review-dialog"' in images.text
    assert 'id="audit-review-image"' in images.text
    assert 'id="audit-review-date"' in images.text
    assert 'id="audit-review-mileage"' in images.text
    assert "Guardar y recalcular" in images.text
    assert 'accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"' in images.text
    assert 'application/pdf' not in images.text
    assert "/js/audit.js" in audit.text
    assert dashboard.status_code == 200
    assert "Últimas validaciones" in dashboard.text
    assert manufacturer_rules.status_code == 200
    assert "Reglas de fabricante" in manufacturer_rules.text
    assert "/js/manufacturer_rules.js" in manufacturer_rules.text


def test_history_validation_screen_exposes_multi_pdf_flow(client: TestClient):
    page = client.get("/validate_document.html")
    javascript = client.get("/js/validation.js")
    stylesheet = client.get("/css/styles.css")

    assert page.status_code == 200
    assert "Validador de mantenimientos PDF" in page.text
    assert 'accept=".pdf,application/pdf"' in page.text
    assert 'id="document-files"' in page.text
    assert 'id="validator-upload-panel"' in page.text
    assert 'aria-controls="validator-upload-panel"' in page.text
    assert "Ampliar tabla" in page.text
    assert "multiple" in page.text
    assert "Agregar documentos" in page.text
    assert "Línea temporal consolidada" in page.text
    assert javascript.status_code == 200
    assert "data-remove-file" in javascript.text
    assert 'API.request("/history-validations/preview"' in javascript.text
    assert "Los PDF y el resultado no se guardaron" in javascript.text
    assert "Abrir documento PDF" in javascript.text
    assert "Ver texto y evidencia técnica" not in javascript.text
    assert "PERIODO DE TOLERANCIA" in javascript.text
    assert "summary.tolerance_period" in javascript.text
    assert ".history-row-tolerance" in stylesheet.text
    assert ".history-status-tolerance" in stylesheet.text


def test_vehicle_crud_and_physical_deletion_without_history(client: TestClient):
    created = client.post(
        "/api/vehicles",
        json={
            "internal_number": "VEH-API-01",
            "plate": "API-001",
            "brand": "Toyota",
            "model": "Hiace",
            "year": 2025,
            "current_odometer": 20,
        },
    )
    assert created.status_code == 201
    vehicle_id = created.json()["id"]
    updated = client.put(f"/api/vehicles/{vehicle_id}", json={"current_odometer": 30})
    assert updated.status_code == 200
    assert updated.json()["current_odometer"] == 30
    deleted = client.delete(f"/api/vehicles/{vehicle_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(f"/api/vehicles/{vehicle_id}").status_code == 404
    assert all(
        vehicle["id"] != vehicle_id for vehicle in client.get("/api/vehicles").json()
    )


def test_contract_vehicle_preserves_supplied_contract_and_is_listed_for_validation(client: TestClient):
    # Un identificador heredado numérico no debe reutilizarse como contrato.
    legacy = client.post(
        "/api/vehicles",
        json={
            "internal_number": "835414",
            "plate": "LEG-001",
            "brand": "Ford",
            "model": "Transit",
            "year": 2024,
            "current_odometer": 0,
        },
    )
    assert legacy.status_code == 201
    assert client.get("/api/vehicles/next-contract").json() == {"numero_contrato": "835415"}

    created = client.post(
        "/api/vehicles",
        json={
            # Un número extraído del PDF debe conservarse como identificador
            # real del contrato.
            "numero_contrato": "111111",
            "brand": "Toyota",
            "model": "Hiace",
            "kilometraje": 42,
            "fecha_factura_origen": "2025-01-15",
            "fecha_inicio_contrato": "2026-01-01",
            "fecha_fin_contrato": "2027-01-01",
        },
    )
    assert created.status_code == 201, created.text
    vehicle = created.json()
    assert vehicle["numero_contrato"] == "111111"
    assert vehicle["kilometraje"] == vehicle["current_odometer"] == 42

    active_vehicles = client.get("/api/vehicles?status=active")
    assert active_vehicles.status_code == 200
    assert any(item["id"] == vehicle["id"] for item in active_vehicles.json())
    assert client.get("/api/vehicles/next-contract").json() == {"numero_contrato": "835415"}


def test_contract_number_is_immutable_and_contract_dates_remain_valid(client: TestClient):
    created = client.post(
        "/api/vehicles",
        json={
            "brand": "Nissan",
            "model": "NP300",
            "kilometraje": 0,
            "fecha_factura_origen": "2025-01-01",
            "fecha_inicio_contrato": "2026-03-01",
            "fecha_fin_contrato": "2027-03-01",
        },
    )
    assert created.status_code == 201
    vehicle_id = created.json()["id"]

    changed_contract = client.put(f"/api/vehicles/{vehicle_id}", json={"numero_contrato": "999999"})
    assert changed_contract.status_code == 422
    assert "no se puede modificar" in changed_contract.json()["detail"]

    invalid_range = client.put(f"/api/vehicles/{vehicle_id}", json={"fecha_fin_contrato": "2026-01-01"})
    assert invalid_range.status_code == 422
    assert "no puede ser anterior" in invalid_range.json()["detail"]


def test_document_rejects_empty_or_unsupported_file(client: TestClient):
    vehicle = client.post(
        "/api/vehicles",
        json={
            "internal_number": "VEH-FILE-01",
            "plate": "FILE-01",
            "brand": "Kia",
            "model": "Bongo",
            "year": 2024,
            "current_odometer": 0,
        },
    ).json()
    response = client.post(
        "/api/documents/upload",
        data={"vehicle_id": str(vehicle["id"])},
        files={"file": ("vacio.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 422
    assert "vacío" in response.json()["detail"]


def test_uploaded_document_can_be_opened_inline_for_manual_review(client: TestClient):
    vehicle = client.post(
        "/api/vehicles",
        json={
            "internal_number": "VEH-PDF-REVIEW",
            "plate": "PDF-001",
            "brand": "Kia",
            "model": "Bongo",
            "year": 2024,
            "current_odometer": 0,
        },
    ).json()
    pdf = b"%PDF-1.4 documento para revision manual"
    uploaded = client.post(
        "/api/documents/upload",
        data={"vehicle_id": str(vehicle["id"])},
        files={"file": ("revision.pdf", pdf, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text

    opened = client.get(f"/api/documents/{uploaded.json()['id']}/file")

    assert opened.status_code == 200
    assert opened.headers["content-type"] == "application/pdf"
    assert opened.content == pdf
    (STORAGE_DIR / uploaded.json()["stored_filename"]).unlink(missing_ok=True)


def test_photo_original_is_available_and_both_corrections_clear_pending_review(client: TestClient):
    vehicle_response = client.post("/api/vehicles", json={
        "brand": "Ford", "model": "Transit", "vehicle_condition": "used",
        "kilometraje": 0, "initial_odometer": 0,
        "fecha_factura_origen": "2026-01-01", "fecha_inicio_contrato": "2026-01-01",
        "fecha_fin_contrato": "2027-01-01",
    })
    assert vehicle_response.status_code == 201, vehicle_response.text
    vehicle_id = vehicle_response.json()["id"]
    content = BytesIO()
    Image.new("RGB", (80, 80), "white").save(content, format="PNG")
    photo = content.getvalue()
    uploaded = client.post("/api/documents/upload", data={"vehicle_id": vehicle_id},
                           files={"file": ("mantenimiento.png", photo, "image/png")})
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    try:
        original = client.get(f"/api/documents/{document['id']}/file")
        assert original.status_code == 200
        assert original.headers["content-type"] == "image/png"
        assert original.content == photo

        pending = client.post("/api/service-events", json={
            "document_id": document["id"], "resets_maintenance_interval": True,
        })
        assert pending.status_code == 201, pending.text
        assert pending.json()["requires_human_review"] is True
        corrected = client.put(f"/api/service-events/{pending.json()['id']}", json={
            "service_date": "2026-05-01", "mileage_km": 9000,
        })
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["requires_human_review"] is False
        assert corrected.json()["user_confirmed"] is True
        saved = client.get(f"/api/service-events/{pending.json()['id']}").json()
        assert saved["service_date"] == "2026-05-01"
        assert saved["mileage_km"] == 9000
        recalculated = client.post("/api/history-validations", json={
            "vehicle_id": vehicle_id, "event_ids": [saved["id"]],
        })
        assert recalculated.status_code == 201, recalculated.text
        assert recalculated.json()["rows"][0]["validation"]["status"] == "COMPLIANT"
        assert client.get(f"/api/documents/{document['id']}/file").content == photo
    finally:
        (STORAGE_DIR / document["stored_filename"]).unlink(missing_ok=True)
