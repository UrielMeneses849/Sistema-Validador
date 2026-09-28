from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    dashboard,
    documents,
    history_validations,
    maintenances,
    manufacturer_rules,
    service_events,
    validations,
    vehicles,
)
from app.core.config import FRONTEND_DIR, ensure_directories
from app.database.init_db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


ensure_directories()
app = FastAPI(
    title="Validador de Documentos de Mantenimiento",
    version="1.0.0",
    description="MVP Fase 1 con reglas deterministas de kilometraje y tiempo.",
    lifespan=lifespan,
)

app.include_router(dashboard.router)
app.include_router(vehicles.router)
app.include_router(manufacturer_rules.router)
app.include_router(maintenances.router)
app.include_router(documents.router)
app.include_router(service_events.router)
app.include_router(history_validations.router)
app.include_router(validations.router)

app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")


@app.get("/", include_in_schema=False)
def maintenance_audit_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/{page_name}.html", include_in_schema=False)
def frontend_page(page_name: str) -> FileResponse:
    allowed_pages = {
        "dashboard",
        "vehicles",
        "manufacturer_rules",
        "register_maintenance",
        "validate_document",
        "results",
        "history",
    }
    if page_name not in allowed_pages:
        raise HTTPException(status_code=404, detail="Pantalla no encontrada.")
    page = FRONTEND_DIR / f"{page_name}.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="Pantalla no encontrada.")
    return FileResponse(page)
