from sqlalchemy import inspect

from app.database.database import Base, engine
from app.models import audit_log, document, document_analysis, maintenance, service_event, validation, vehicle  # noqa: F401


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _apply_sqlite_compatibility_migrations()


def _apply_sqlite_compatibility_migrations() -> None:
    """Conserva el historial de instalaciones SQLite creadas por el MVP anterior."""
    if engine.dialect.name != "sqlite":
        return
    columns = {column["name"] for column in inspect(engine).get_columns("validations")}
    if "analysis_details" not in columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE validations ADD COLUMN analysis_details JSON")


if __name__ == "__main__":
    init_db()
