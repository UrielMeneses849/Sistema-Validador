# Validador de documentos de mantenimiento

MVP para registrar vehículos, cargar evidencia documental y reconstruir el historial de mantenimiento. Los PDFs digitales se analizan directamente y las imágenes JPEG, PNG o WEBP pueden procesarse localmente con Tesseract. La captura manual se conserva para corregir datos dudosos o no detectados.

La regla central es: **10,000 km o 6 meses de calendario, lo que ocurra primero**. No incluye OCR, IA ni conexiones externas.

## Decisiones técnicas

- FastAPI sirve tanto la API REST como el frontend multipantalla HTML/CSS/JavaScript puro.
- SQLite es la base por defecto. La URL se lee de `DATABASE_URL`, por lo que el cambio posterior a PostgreSQL queda aislado en la configuración y el driver correspondiente.
- Los documentos se conservan con un nombre UUID en `storage/documents/`; nunca se sobrescriben ni se transforman.
- `pypdf` lee directamente la capa textual de PDFs digitales. Tesseract sólo se usa para imágenes y nunca reemplaza una corrección humana confirmada.
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

La portada muestra la auditoría visual del historial. El dashboard operativo anterior se conserva en [http://127.0.0.1:8000/dashboard.html](http://127.0.0.1:8000/dashboard.html) y también está disponible desde la navegación lateral.

En la primera ejecución se crea automáticamente `data/validator.db` y la carpeta de documentos. Las tablas nuevas se agregan sin borrar el historial existente de SQLite.

## OCR local con Tesseract

El paquete Python `pytesseract` no incluye el ejecutable del motor. En Windows puede instalarse con Winget:

```powershell
winget install --id tesseract-ocr.tesseract --exact
```

En macOS con Homebrew, instala manualmente el motor y los idiomas adicionales:

```bash
brew install tesseract
brew install tesseract-lang
tesseract --list-langs
```

La lista debe incluir `spa` y `eng`. El repositorio no instala software del sistema automáticamente. Si Tesseract o los idiomas no están disponibles, `POST /api/documents/{id}/analyze` responde `503` con instrucciones y nunca sustituye el resultado por fixtures.

Configuración opcional exclusivamente del servidor:

| Variable | Predeterminado | Uso |
|---|---:|---|
| `OCR_PROVIDER` | `tesseract` | Proveedor visual activo. |
| `OCR_LANG` | `spa+eng` | Idiomas enviados a Tesseract. |
| `TESSERACT_CMD` | autodetección | Ruta opcional al ejecutable. |
| `TESSDATA_DIR` | `data/tessdata` | Carpeta opcional con los modelos `.traineddata`. |
| `OCR_REVIEW_THRESHOLD` | `0.88` | Umbral de revisión por campo. |
| `OCR_MAX_FILE_SIZE_MB` | `10` | Tamaño máximo por imagen. |
| `OCR_MAX_IMAGES_PER_ANALYSIS` | `15` | Límite coordinado con la UI. |
| `OCR_MAX_IMAGE_PIXELS` | `40000000` | Protección contra imágenes excesivas. |

La portada permite procesar hasta 15 imágenes con dos solicitudes concurrentes. Cada archivo usa los endpoints de carga y análisis existentes; por ello un fallo no elimina los resultados de los demás.

## Análisis automático de PDF digital

1. Registra o selecciona el vehículo.
2. En **Validar documento**, elige el PDF y presiona **Analizar y validar documento**.
3. El sistema extrae primero el texto digital del PDF, clasifica el documento y detecta los eventos de servicio.
4. La pantalla muestra los valores detectados y registra el resultado automáticamente. Puedes editar los campos y presionar **Guardar correcciones y revalidar**.

Para una imagen, `HybridExtractor` delega en `VisionExtractor` y el proveedor Tesseract configurado. Los PDF escaneados se rasterizan localmente con `pypdfium2` antes de pasar por OCR.

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

## Siguientes fases

`VisionExtractor` y `OcrExtractor` permanecen desacoplados del parser. Los historiales digitales se interpretan con palabras y coordenadas: cabecera de tabla → columnas → filas → eventos; la fecha de generación queda separada de las fechas de servicio. Quedan pendientes la rasterización de PDF escaneado, colas asíncronas, deduplicación/fusión avanzada, autenticación multiagencia y métricas operativas del proveedor.
