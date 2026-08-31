from __future__ import annotations

import os
import sys
from pathlib import Path

# Debe establecerse antes de importar la aplicación, que crea el engine.
os.environ["DATABASE_URL"] = "sqlite:////private/tmp/validador_mantenimiento_tests.db"
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import pytest
from fastapi.testclient import TestClient

from app.database.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
