from sqlalchemy import inspect

from app.database.database import Base, engine
from app.models import (  # noqa: F401
    audit_log,
    document,
    document_analysis,
    manufacturer_maintenance_rule,
    maintenance,
    service_event,
    validation,
    vehicle,
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _apply_sqlite_compatibility_migrations()


def _apply_sqlite_compatibility_migrations() -> None:
    """Conserva el historial de instalaciones SQLite creadas por el MVP anterior."""
    if engine.dialect.name != "sqlite":
        return
    validation_columns = {column["name"] for column in inspect(engine).get_columns("validations")}
    if "analysis_details" not in validation_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE validations ADD COLUMN analysis_details JSON")

    _migrate_sqlite_vehicles()
    _add_val002_columns()


def _add_val002_columns() -> None:
    """Agrega campos de VAL-002 sin inferir datos históricos ni borrar registros."""
    vehicle_columns = {column["name"] for column in inspect(engine).get_columns("vehicles")}
    maintenance_columns = {column["name"] for column in inspect(engine).get_columns("maintenances")}
    with engine.begin() as connection:
        if "vehicle_condition" not in vehicle_columns:
            connection.exec_driver_sql(
                "ALTER TABLE vehicles ADD COLUMN vehicle_condition VARCHAR(20) "
                "NOT NULL DEFAULT 'unknown'"
            )
        if "initial_odometer" not in vehicle_columns:
            connection.exec_driver_sql("ALTER TABLE vehicles ADD COLUMN initial_odometer INTEGER")
        if "resets_maintenance_interval" not in maintenance_columns:
            connection.exec_driver_sql(
                "ALTER TABLE maintenances ADD COLUMN resets_maintenance_interval BOOLEAN "
                "NOT NULL DEFAULT 1"
            )


_VEHICLE_NEW_COLUMNS = {
    "numero_contrato",
    "kilometraje",
    "fecha_factura_origen",
    "fecha_inicio_contrato",
    "fecha_fin_contrato",
}
_VEHICLE_NULLABLE_LEGACY_COLUMNS = {"internal_number", "plate", "year"}
_VEHICLE_SOURCE_COLUMNS = {
    "id",
    "internal_number",
    "plate",
    "vin",
    "brand",
    "model",
    "year",
    "current_odometer",
    "status",
    "created_at",
    "updated_at",
}
_VEHICLE_COLUMNS_IN_ORDER = (
    "id",
    "internal_number",
    "plate",
    "vin",
    "brand",
    "model",
    "year",
    "current_odometer",
    "status",
    "numero_contrato",
    "kilometraje",
    "fecha_factura_origen",
    "fecha_inicio_contrato",
    "fecha_fin_contrato",
    "created_at",
    "updated_at",
)
_VEHICLE_TEMPORARY_TABLE = "vehicles__contract_migration"


def _migrate_sqlite_vehicles() -> None:
    """Actualiza ``vehicles`` sin cambiar sus PK ni las FK que la referencian.

    SQLite permite agregar columnas, pero no relajar un ``NOT NULL``. Las
    instalaciones previas requieren que placa, año y número interno sean
    obligatorios; por ello se reconstruye la tabla cuando es necesario y se
    copian los IDs originales antes de renombrarla.
    """
    vehicle_columns = {column["name"]: column for column in inspect(engine).get_columns("vehicles")}
    missing_source_columns = _VEHICLE_SOURCE_COLUMNS.difference(vehicle_columns)
    if missing_source_columns:
        missing = ", ".join(sorted(missing_source_columns))
        raise RuntimeError(f"No se puede migrar vehicles: faltan columnas base ({missing}).")

    needs_rebuild = (
        not _VEHICLE_NEW_COLUMNS.issubset(vehicle_columns)
        or any(not vehicle_columns[name]["nullable"] for name in _VEHICLE_NULLABLE_LEGACY_COLUMNS)
    )
    if not needs_rebuild:
        with engine.begin() as connection:
            _ensure_vehicle_indexes(connection)
        return

    # La desactivación es local a esta conexión y sólo cubre el intercambio de
    # tablas. Así se mantienen los hijos que ya apuntan a vehicles.id.
    with engine.connect() as connection:
        foreign_keys_were_enabled = bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
        connection.commit()
        if foreign_keys_were_enabled:
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.commit()

        try:
            with connection.begin():
                _rebuild_sqlite_vehicles(connection, vehicle_columns)
                _ensure_vehicle_indexes(connection)
                _assert_vehicle_foreign_keys(connection)
        finally:
            if foreign_keys_were_enabled:
                connection.exec_driver_sql("PRAGMA foreign_keys = ON")
                connection.commit()


def _rebuild_sqlite_vehicles(connection, vehicle_columns: dict[str, dict]) -> None:
    temporary_table_exists = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_VEHICLE_TEMPORARY_TABLE,),
    ).scalar()
    if temporary_table_exists:
        raise RuntimeError(
            "No se puede migrar vehicles porque existe una tabla temporal de una migración anterior."
        )

    connection.exec_driver_sql(
        f"""
        CREATE TABLE {_VEHICLE_TEMPORARY_TABLE} (
            id INTEGER NOT NULL,
            internal_number VARCHAR(60),
            plate VARCHAR(30),
            vin VARCHAR(80),
            brand VARCHAR(80) NOT NULL,
            model VARCHAR(80) NOT NULL,
            year INTEGER,
            current_odometer INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL,
            numero_contrato VARCHAR(6),
            kilometraje INTEGER,
            fecha_factura_origen DATE,
            fecha_inicio_contrato DATE,
            fecha_fin_contrato DATE,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )

    source_values = [
        f'"{column}"' if column in vehicle_columns else "NULL"
        for column in _VEHICLE_COLUMNS_IN_ORDER
    ]
    columns_sql = ", ".join(_VEHICLE_COLUMNS_IN_ORDER)
    source_sql = ", ".join(source_values)
    connection.exec_driver_sql(
        f"INSERT INTO {_VEHICLE_TEMPORARY_TABLE} ({columns_sql}) "
        f"SELECT {source_sql} FROM vehicles"
    )

    original_count = connection.exec_driver_sql("SELECT COUNT(*) FROM vehicles").scalar_one()
    copied_count = connection.exec_driver_sql(
        f"SELECT COUNT(*) FROM {_VEHICLE_TEMPORARY_TABLE}"
    ).scalar_one()
    if original_count != copied_count:
        raise RuntimeError("La migración de vehicles no copió todos los registros.")

    connection.exec_driver_sql("DROP TABLE vehicles")
    connection.exec_driver_sql(f"ALTER TABLE {_VEHICLE_TEMPORARY_TABLE} RENAME TO vehicles")


def _ensure_vehicle_indexes(connection) -> None:
    """Restaura los índices del modelo, eliminados al reconstruir la tabla."""
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_vehicles_internal_number ON vehicles (internal_number)"
    )
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_vehicles_plate ON vehicles (plate)")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_vehicles_vin ON vehicles (vin)")
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_vehicles_numero_contrato ON vehicles (numero_contrato)"
    )


def _assert_vehicle_foreign_keys(connection) -> None:
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        raise RuntimeError("La migración de vehicles dejó relaciones foráneas inválidas.")


if __name__ == "__main__":
    init_db()
