# Validador de documentos de mantenimiento

MVP para registrar vehículos, cargar evidencia documental y reconstruir el historial de mantenimiento. Los PDFs digitales se analizan automáticamente; la captura manual se conserva únicamente para corregir datos no detectados.

La regla central es: **10,000 km o 6 meses de calendario, lo que ocurra primero**. No incluye OCR, IA ni conexiones externas.

## Decisiones técnicas

- FastAPI sirve tanto la API REST como el frontend multipantalla HTML/CSS/JavaScript puro.
- SQLite es la base por defecto. La URL se lee de `DATABASE_URL`, por lo que el cambio posterior a PostgreSQL queda aislado en la configuración y el driver correspondiente.
- Los documentos se conservan con un nombre UUID en `storage/documents/`; nunca se sobrescriben ni se transforman.
- `pypdf` lee directamente la capa textual de PDFs digitales. No se usa OCR ni se convierten a imagen cuando hay texto útil.
- Un documento puede producir varios `ServiceEvent`; cada valor conserva evidencia, valor original, normalización, página y método de extracción.
- Cada validación guarda un resultado y los eventos de auditoría asociados.

## Requisitos

- Python 3.13 o superior (el código evita características exclusivas para facilitar desarrollo temporal en Python 3.9+).
- `pip`.

## Ejecución desde cero

Desde la carpeta `validador_mantenimiento`:

```bash
python3.13 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abre [http://127.0.0.1:8000](http://127.0.0.1:8000). La documentación interactiva de la API está en [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

En la primera ejecución se crea automáticamente `data/validator.db` y la carpeta de documentos. Las tablas nuevas se agregan sin borrar el historial existente de SQLite.

## Análisis automático de PDF digital

1. Registra o selecciona el vehículo.
2. En **Validar documento**, elige el PDF y presiona **Analizar y validar documento**.
3. El sistema extrae primero el texto digital del PDF, clasifica el documento y detecta los eventos de servicio.
4. La pantalla muestra los valores detectados y registra el resultado automáticamente. Puedes editar los campos y presionar **Guardar correcciones y revalidar**.

Para un PDF escaneado o una imagen, `HybridExtractor` deja el documento listo para un `VisionExtractor` futuro y conserva la corrección manual como fallback. No hay OCR configurado en este MVP.

## Flujo de uso

1. Registra un vehículo en **Vehículos**.
2. Registra su mantenimiento de referencia en **Último mantenimiento**.
3. Abre **Validar documento**, selecciona el vehículo, carga un PDF/JPG/JPEG/PNG e introduce los datos visibles.
4. El resultado se guarda de inmediato. El detalle muestra límites, condiciones y trazabilidad.
5. Consulta y filtra los resultados desde **Historial**.

## API principal

- `POST`, `GET` `/api/vehicles`; `GET`, `PUT`, `DELETE` `/api/vehicles/{id}` (DELETE es baja lógica).
- `POST`, `GET` `/api/vehicles/{id}/maintenances`.
- `POST /api/documents/upload`, `GET /api/documents/{id}`, `POST /api/documents/{id}/analyze` y `GET /api/documents/{id}/analysis`. Para corregir una extracción automática no confirmada: `POST /api/documents/{id}/analyze?force=true`.
- `GET`, `PUT` `/api/service-events/{id}` y `POST /api/service-events/{id}/validate`.
- `POST`, `GET` `/api/validations`; `GET /api/validations/{id}` y `GET /api/validations/{id}/audit`.
- `GET /api/dashboard`.

## Pruebas

Con el entorno virtual activo:

```bash
pytest -q
```

Las pruebas cubren los casos de la versión manual, extracción directa de PDF, primer mantenimiento disponible, intervalos cumplidos/excedidos y kilometraje regresivo.

## Preparación para Fase 2

`VisionExtractor` y `OcrExtractor` son contratos desacoplados, preparados para documentos escaneados, fotos y manuscritos. Los historiales digitales se interpretan con palabras y coordenadas: cabecera de tabla → columnas → filas → eventos; la fecha de generación queda separada de las fechas de servicio. Quedan pendientes el proveedor visual, preprocesamiento no destructivo, deduplicación/fusión avanzada y revisión humana enriquecida.
