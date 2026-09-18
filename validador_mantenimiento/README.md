# Validador de documentos de mantenimiento

Sistema para registrar vehículos, cargar evidencia documental y reconstruir el historial de mantenimiento. Los PDF digitales se analizan directamente; las fotografías JPEG, PNG y WEBP pasan por un lector local especializado en **fecha del servicio + kilometraje**. La captura manual se conserva para corregir datos dudosos o no detectados.

La regla central para Seminuevo (M2) es **10,000 km o 6 meses de calendario, lo que ocurra primero**, más la tolerancia configurada. Nuevo (M1) usa la política activa del fabricante. El OCR funciona sin servicios externos y no hay dependencia ni llamada a OpenAI.

## Decisiones técnicas

- FastAPI sirve tanto la API REST como el frontend multipantalla HTML/CSS/JavaScript puro.
- SQLite es la base por defecto. La URL se lee de `DATABASE_URL`, por lo que el cambio posterior a PostgreSQL queda aislado en la configuración y el driver correspondiente.
- Los documentos se conservan con un nombre UUID en `storage/documents/`; nunca se sobrescriben ni se transforman.
- `pypdf`/`pdfplumber` leen la capa textual de PDF y `pypdfium2` rasteriza PDF escaneado. Tesseract sólo se usa localmente y nunca reemplaza una corrección humana confirmada.
- El lector de imágenes separa preprocesamiento, layout, cuadros de servicio, crops, reconocimiento, consenso, revisión y persistencia. OpenCV mejora perspectiva, deskew, sombras, CLAHE y umbral adaptativo; existe fallback Pillow cuando OpenCV no está disponible.
- TrOCR es opcional y se carga exclusivamente desde un directorio local. La app inicia sin Torch/Transformers y expone el diagnóstico en `GET /api/documents/ocr-diagnostics`.
- Un documento puede producir varios `ServiceEvent`; cada valor conserva evidencia, valor original, normalización, página y método de extracción.
- Cada validación guarda un resultado y los eventos de auditoría asociados.

## Requisitos

- Python 3.13 o superior.
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

## OCR local especializado

El paquete Python `pytesseract` no incluye el ejecutable del motor. En macOS con Homebrew, instala manualmente el motor y los idiomas adicionales:

```bash
brew install tesseract
brew install tesseract-lang
tesseract --list-langs
```

La lista debe incluir `spa` y `eng`. El repositorio no instala software del sistema automáticamente. Si Tesseract o los idiomas no están disponibles, `POST /api/documents/{id}/analyze` responde `503` con instrucciones y nunca sustituye el resultado por fixtures.

Pipeline para una fotografía:

1. EXIF, rotación de texto, perspectiva y deskew.
2. Variantes `original`, `grayscale`, `high_contrast`, `adaptive_threshold`, `shadow_normalized` y `blue_reduced`.
3. Detección genérica de cuadros mediante etiquetas y, si OpenCV está disponible, líneas/rectángulos.
4. Recortes mínimos de Fecha y Kilometraje.
5. OCR de cada recorte sobre todas las variantes.
6. Consenso: confianza OCR 35%, acuerdo entre variantes 35%, validez de formato 20% y cercanía a etiqueta 10%.
7. Revisión humana si el par queda debajo de `OCR_REVIEW_THRESHOLD`, es ambiguo, futuro, improbable o regresivo.
8. Conversión al mismo `ServiceEvent` usado por PDF y validación VAL-002.

Una imagen puede generar varios eventos. Los cuadros sin ninguna lectura de fecha/km se ignoran. El original nunca se transforma; sólo se conservan crops mínimos como evidencia.

Configuración opcional exclusivamente del servidor:

| Variable | Predeterminado | Uso |
|---|---:|---|
| `OCR_PROVIDER` | `tesseract` | Proveedor visual activo. |
| `OCR_LANG` | `spa+eng` | Idiomas enviados a Tesseract. |
| `TESSERACT_CMD` | autodetección | Ruta opcional al ejecutable. |
| `OCR_REVIEW_THRESHOLD` | `0.88` | Umbral de revisión por campo. |
| `OCR_MAX_FILE_SIZE_MB` | `10` | Tamaño máximo por imagen. |
| `OCR_MAX_IMAGES_PER_ANALYSIS` | `15` | Límite coordinado con la UI. |
| `OCR_MAX_IMAGE_PIXELS` | `40000000` | Protección contra imágenes excesivas. |
| `HANDWRITING_MODEL_PATH` | vacío | Directorio local de un TrOCR promovido explícitamente. |
| `OCR_DEBUG` | `false` | Guarda etapas visuales y candidatos cuando vale `1`, `true`, `yes` u `on`. |
| `OCR_DEBUG_DIR` | `debug_ocr/` | Carpeta local de diagnóstico, ignorada por Git. |

La portada permite mezclar hasta 15 PDF/imágenes con dos solicitudes concurrentes. Cada archivo muestra cantidad de eventos o revisión requerida; un fallo no elimina los resultados de los demás.

### Diagnóstico visual de una fotografía

El modo normal no conserva artefactos de diagnóstico. Para depurar una carga realizada desde la aplicación, inicia el servidor con:

```bash
OCR_DEBUG=1 uvicorn app.main:app --reload
```

Cada análisis de imagen queda en `debug_ocr/<document_id>/`: original orientado, corrección de perspectiva, variantes de página, overlay de bounding boxes, cuadro completo, crops de fecha/km en cada variante, `layout.json` y `ocr-report.json` con texto crudo y candidatos por variante.

También puede ejecutarse sin modificar la base de datos:

```bash
python -m training.debug_ocr_image ruta/foto.jpeg --run-id prueba-001
```

La localización combina rectángulos con las etiquetas `Fecha`, `DATE`, `Kilometraje`, `KM`, `Km/Millas` y variantes equivalentes. Los límites de etiquetas vecinas se usan para que un crop no invada otro cuadro.

## Revisión humana y dataset

Los eventos inciertos muestran los recortes de fecha y kilometraje. **Confirmar lectura** o editar un campo marca `user_confirmed`, conserva la predicción original, vuelve a calcular la cronología VAL-002 y guarda únicamente los crops confirmados en:

```text
training_data/
  crops/dates/
  crops/mileage/
  annotations/verified.jsonl
```

El identificador combina hash de imagen, tipo de campo y valor verificado, por lo que una confirmación idéntica no se duplica. `page_group` mantiene todos los crops de una página en el mismo split.

```bash
# Importar crops previamente recortados y etiquetados
python -m training.import_examples --manifest ejemplos.jsonl

# Exportar sólo confirmaciones humanas con split reproducible
python -m training.export_dataset --seed 20260916 --output training_data/exports/v1

# Evaluar predicciones ya calculadas sin instalar ML
python -m training.evaluate_model --predictions predicciones.jsonl
```

Las métricas son `DATE_EXACT_ACCURACY`, `MILEAGE_EXACT_ACCURACY`, `PAIR_EXACT_ACCURACY`, `HUMAN_REVIEW_RATE` y `FALSE_ACCEPT_RATE`.

## Benchmark de fotografías reales

El ground truth real se mantiene separado de las correcciones de entrenamiento en `benchmark_data/real_photos/` y está ignorado por Git. Una imagen puede registrar varios servicios:

```bash
python -m training.label_real_fixture ruta/foto.jpeg

# Alternativa no interactiva
python -m training.label_real_fixture ruta/foto.jpeg \
  --event 2025-10-24 88913 \
  --event 2026-05-10 100000

python -m training.evaluate_real_benchmark \
  --save-predictions benchmark_data/real_photos/predictions.jsonl \
  --output benchmark_data/real_photos/report.json
```

El reporte incluye cuadros detectados, eventos esperados/detectados, exactitud exacta por campo y par, revisión humana, falsas aceptaciones y fallos detallados por imagen. Estas fotografías no deben reutilizarse para entrenamiento.

## TrOCR opcional y fine-tuning

Torch/Transformers no forman parte del entorno principal. Crea otro venv, instala `requirements-ocr-ml.txt` y proporciona un checkpoint base que ya exista localmente:

```bash
python -m training.train_handwriting_model \
  --dataset training_data/exports/v1 \
  --base-model /ruta/local/trocr-base-handwritten

python -m training.evaluate_model \
  --dataset training_data/exports/v1/test.jsonl \
  --model models/maintenance_handwriting_v1
```

El dispositivo se selecciona MPS → CUDA → CPU. Cada entrenamiento crea `maintenance_handwriting_vN` y **no lo activa**. Tras evaluar especialmente `FALSE_ACCEPT_RATE`, se promueve manualmente configurando `HANDWRITING_MODEL_PATH`. La inferencia usa `local_files_only=True` y nunca descarga modelos durante la aplicación normal.

## Análisis automático de PDF digital

1. Registra o selecciona el vehículo.
2. En **Validar documento**, elige el PDF y presiona **Analizar y validar documento**.
3. El sistema extrae primero el texto digital del PDF, clasifica el documento y detecta los eventos de servicio.
4. La pantalla muestra los valores detectados y registra el resultado automáticamente. Puedes editar los campos y presionar **Guardar correcciones y revalidar**.

Para una imagen, `HybridExtractor` delega en `VisionExtractor` y el lector especializado. Los PDF escaneados se rasterizan por página y continúan usando el OCR documental general para no alterar el flujo PDF existente.

## Flujo de uso

1. Registra un vehículo en **Vehículos**.
2. Registra su mantenimiento de referencia en **Último mantenimiento**.
3. En la auditoría, selecciona el vehículo y carga PDF/JPG/JPEG/PNG/WEBP mezclados.
4. Confirma o corrige sólo los registros que lo requieran; el detalle muestra límites, condiciones y trazabilidad.
5. Consulta y filtra los resultados desde **Historial**.

## API principal

- `POST`, `GET` `/api/vehicles`; `GET`, `PUT`, `DELETE` `/api/vehicles/{id}` (DELETE es baja lógica).
- `POST`, `GET` `/api/vehicles/{id}/maintenances`.
- `POST /api/documents/upload`, `GET /api/documents/{id}`, `POST /api/documents/{id}/analyze` y `GET /api/documents/{id}/analysis`. Para corregir una extracción automática no confirmada: `POST /api/documents/{id}/analyze?force=true`.
- `GET`, `PUT` `/api/service-events/{id}`, `POST /api/service-events/{id}/confirm`, `GET /api/service-events/{id}/evidence/{date|mileage_km}` y `POST /api/service-events/{id}/validate`.
- `POST`, `GET` `/api/validations`; `GET /api/validations/{id}` y `GET /api/validations/{id}/audit`.
- `GET /api/dashboard`.

## Pruebas

Con el entorno virtual activo:

```bash
pytest -q
```

Las pruebas cubren la versión manual, PDF, parser especializado, múltiples cuadros, rotación, formatos fecha/km, consenso conservador, cuadros vacíos, dataset deduplicado, splits por página, métricas, revisión humana y regresiones de VAL-002. Las fotografías reales deben mantenerse separadas entre entrenamiento y evaluación; los fixtures sintéticos verifican el software, no representan una cifra de precisión de producción.

## Siguientes fases

`MaintenanceImageRecognizer`, `LocalMaintenanceImageRecognizer` y `ExternalVisionFallback` mantienen el reconocimiento desacoplado de VAL-002. En esta versión el fallback externo siempre es `None` y no existe implementación de proveedor, HTTP ni almacenamiento de claves. La mejora de manuscritos/sellos depende de acumular fotografías reales confirmadas y reservar páginas completas para validation/test.
