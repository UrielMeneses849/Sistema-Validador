const AUDIT_LIMITS = Object.freeze({ maxFiles: 15, maxFileSize: 10 * 1024 * 1024, dateMonths: 6, mileageKm: 10000 })

// Fixture de demostración: no procede de OCR ni se persiste en el backend.
const AUDIT_DEMO_RECORDS = Object.freeze([
  { id: "demo-01", date: "2023-01-24", mileage: 10750, sourceFileName: "libro-1.jpg", sourceType: "image", confidence: 0.96, reviewStatus: "confirmed", manuallyVerified: false, rawExtractedText: "24/01/2023 · 10,750 km" },
  { id: "demo-02", date: "2023-08-15", mileage: 30294, sourceFileName: "libro-2.jpg", sourceType: "image", confidence: 0.93, reviewStatus: "confirmed", manuallyVerified: false, rawExtractedText: "15/08/2023 · 30,294 km" },
  { id: "demo-03", date: "2024-01-06", mileage: 40123, sourceFileName: "sello-tenue.jpg", sourceType: "image", confidence: 0.74, reviewStatus: "review", manuallyVerified: false, rawExtractedText: "06/01/2024 · 40?23 km" },
])

const auditState = { files: [], records: [], vehicles: [], history: null, showingResults: false, controller: null, failedItems: [], runId: null, saving: false, demo: false }
const dropzone = document.querySelector("#audit-dropzone")
const fileInput = document.querySelector("#audit-files")
const uploadError = document.querySelector("#audit-upload-error")
const previewsPanel = document.querySelector("#audit-previews")
const demoNote = document.querySelector("#audit-demo-note")
const exampleButton = document.querySelector("#audit-example")
const resultPanel = document.querySelector("#audit-result")
const newAction = document.querySelector("#audit-new-action")
const vehicleSelect = document.querySelector("#audit-vehicle")
const analyzeButton = document.querySelector("#audit-analyze")
const retryButton = document.querySelector("#audit-retry")
const processStatus = document.querySelector("#audit-process-status")
const progressElement = document.querySelector("#audit-progress")
const progressLabel = document.querySelector("#audit-progress-label")
const currentFile = document.querySelector("#audit-current-file")
const partialError = document.querySelector("#audit-partial-error")
const reviewDialog = document.querySelector("#audit-review-dialog")
const reviewForm = document.querySelector("#audit-review-form")
const reviewImage = document.querySelector("#audit-review-image")
const reviewPdf = document.querySelector("#audit-review-pdf")
const reviewDate = document.querySelector("#audit-review-date")
const reviewMileage = document.querySelector("#audit-review-mileage")
const reviewNotice = document.querySelector("#audit-review-notice")
let reviewRecordId = null
let reviewDraftRecord = null
let reviewZoom = 1

function auditEscape(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#039;", '"': "&quot;" })[character])
}

function auditDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return null
  const [year, month, day] = value.split("-").map(Number)
  const date = new Date(year, month - 1, day)
  return Number.isNaN(date.getTime()) || date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day ? null : date
}

function formatDate(value) {
  const date = auditDate(value)
  return date ? new Intl.DateTimeFormat("es-MX", { day: "2-digit", month: "2-digit", year: "numeric" }).format(date) : "Sin fecha"
}

function formatKm(value) {
  return Number.isFinite(Number(value)) ? `${Number(value).toLocaleString("es-MX")} km` : "—"
}

function addMonths(date, months) {
  const result = new Date(date)
  const targetMonth = result.getMonth() + months
  result.setDate(1)
  result.setMonth(targetMonth)
  result.setDate(Math.min(date.getDate(), new Date(result.getFullYear(), result.getMonth() + 1, 0).getDate()))
  return result
}

function differenceDays(from, to) {
  return Math.round((Date.UTC(to.getFullYear(), to.getMonth(), to.getDate()) - Date.UTC(from.getFullYear(), from.getMonth(), from.getDate())) / 86400000)
}

function calculateAudit(records) {
  const sortedRecords = [...records].sort((left, right) => (left.date || "9999").localeCompare(right.date || "9999"))
  const completeRecords = sortedRecords.filter((record) => record.mileage !== "" && auditDate(record.date) && Number.isFinite(Number(record.mileage)) && Number(record.mileage) >= 0)
  const chronologicalIntervals = completeRecords.slice(0, -1).map((from, index) => {
    const to = completeRecords[index + 1]
    const fromDate = auditDate(from.date)
    const toDate = auditDate(to.date)
    const dateLimit = addMonths(fromDate, AUDIT_LIMITS.dateMonths)
    const deltaDays = differenceDays(fromDate, toDate)
    const allowedDays = differenceDays(fromDate, dateLimit)
    const deltaMileage = Number(to.mileage) - Number(from.mileage)
    const withinDate = deltaDays >= 0 && toDate <= dateLimit
    const withinMileage = deltaMileage >= 0 && deltaMileage <= AUDIT_LIMITS.mileageKm
    const failedRules = [!withinDate && "fecha", !withinMileage && "kilometraje"].filter(Boolean)
    return {
      from, to, deltaDays, deltaMileage, withinDate, withinMileage,
      excessDays: Math.max(0, deltaDays - allowedDays),
      excessMileage: Math.max(0, deltaMileage - AUDIT_LIMITS.mileageKm),
      message: failedRules.length ? `El intervalo excede la regla de ${failedRules.join(" y ")}.` : "El intervalo cumple ambas reglas.",
    }
  })
  const incomplete = completeRecords.length !== sortedRecords.length
  const needsConfirmation = sortedRecords.some((record) => record.reviewStatus === "review")
  const failedInterval = chronologicalIntervals.some((interval) => !interval.withinDate || !interval.withinMileage)
  const insufficient = sortedRecords.length < 2
  return { records: sortedRecords, chronologicalIntervals, overallStatus: incomplete || insufficient || needsConfirmation || failedInterval ? "review" : "compliant" }
}

function validateFiles(files) {
  return MaintenanceAnalysisAPI.validateFiles(files, "image")
}

function updateAnalyzeAvailability() {
  analyzeButton.disabled = !auditState.files.length || !vehicleSelect.value || Boolean(auditState.controller)
  fileInput.disabled = Boolean(auditState.controller)
  vehicleSelect.disabled = Boolean(auditState.controller) || auditState.records.some((record) => record.eventId)
  document.querySelector("#audit-file-trigger").disabled = Boolean(auditState.controller)
}

function showPartialError(message) {
  partialError.textContent = message
  partialError.classList.toggle("show", Boolean(message))
}

function receiveFiles(fileList) {
  if (auditState.controller) return
  const incoming = Array.from(fileList || [])
  if (!incoming.length) return
  const error = validateFiles([...auditState.files.map((item) => item.file), ...incoming])
  uploadError.textContent = error
  dropzone.classList.toggle("has-error", Boolean(error))
  if (error) return
  auditState.files.push(...incoming.map((file) => ({ id: `${file.name}-${file.lastModified}-${crypto.randomUUID()}`, file, previewUrl: URL.createObjectURL(file), documentId: null, status: "pending" })))
  fileInput.value = ""
  renderPreviews()
  updateAnalyzeAvailability()
}

function renderPreviews() {
  previewsPanel.replaceChildren()
  const hasFiles = auditState.files.length > 0
  previewsPanel.classList.toggle("hidden", !hasFiles)
  demoNote.classList.toggle("hidden", !hasFiles)
  if (!hasFiles) return

  const heading = document.createElement("div")
  heading.className = "audit-previews-heading"
  const completed = auditState.files.filter((item) => item.status === "completed").length
  heading.innerHTML = `<strong>${auditState.files.length} ${auditState.files.length === 1 ? "archivo" : "archivos"}</strong><span>${completed ? `${completed} procesados` : "Listos para análisis local"}</span>`
  const list = document.createElement("div")
  list.className = "audit-preview-list"
  auditState.files.forEach((item) => {
    const preview = document.createElement("div")
    preview.className = "audit-preview"
    const image = item.file.type === "application/pdf" ? document.createElement("div") : document.createElement("img")
    if (item.file.type === "application/pdf") {
      image.className = "audit-preview-document"
      image.textContent = "PDF"
      image.setAttribute("aria-label", `Documento PDF ${item.file.name}`)
    } else {
      image.src = item.previewUrl
      image.alt = `Vista previa de ${item.file.name}`
    }
    const remove = document.createElement("button")
    remove.type = "button"
    remove.dataset.fileId = item.id
    remove.setAttribute("aria-label", `Quitar ${item.file.name}`)
    remove.textContent = "×"
    const name = document.createElement("span")
    name.title = item.file.name
    name.textContent = item.file.name
    const state = document.createElement("small")
    state.className = `audit-preview-state ${item.status}`
    const completedText = item.requiresReview
      ? "Revisión humana requerida"
      : `${item.eventCount || 0} ${item.eventCount === 1 ? "mantenimiento detectado" : "mantenimientos detectados"}`
    state.textContent = item.status === "completed" ? completedText : ({ pending: "Pendiente", processing: "Procesando", failed: "Error", unreadable: "Sin lectura automática · revisar foto" }[item.status] || "Pendiente")
    preview.append(image, remove, name, state)
    if (item.documentId && !auditState.controller) {
      const manual = document.createElement("button")
      manual.type = "button"
      manual.className = "button button-secondary"
      manual.textContent = "Ver foto y agregar mantenimiento"
      manual.disabled = auditState.saving
      manual.addEventListener("click", () => {
        reviewDraftRecord = { id: `capture-${crypto.randomUUID()}`, documentId: item.documentId,
          date: "", mileage: "", sourceFileName: item.file.name, sourceType: "image",
          reviewStatus: "review", originalUrl: `/api/documents/${item.documentId}/file` }
        openPhotoReview(reviewDraftRecord)
      })
      preview.append(manual)
    }
    list.append(preview)
  })
  previewsPanel.append(heading, list)
}

function statusMarkup(record) {
  if (record.reviewStatus === "review") return `<span class="audit-badge audit-badge-review">△ Revisar · ${Math.round((record.confidence || 0) * 100)}%</span>`
  if (record.reviewStatus === "manual" || record.manuallyVerified) return '<span class="audit-badge audit-badge-manual">✓ Revisión manual</span>'
  return `<span class="audit-badge audit-badge-high">✓ Alta confianza · ${Math.round((record.confidence || 0) * 100)}%</span>`
}

function resultMarkup(record) {
  const status = record.validationStatus
  if (!status) return statusMarkup(record)
  const labels = {
    COMPLIANT: "Cumple", TOLERANCE_PERIOD: "En tolerancia", REQUIRES_REVIEW: "Revisión",
    INSUFFICIENT_DATA: "Datos insuficientes", EXCEEDED_TIME: "Excede tiempo",
    EXCEEDED_MILEAGE: "Excede km", EXCEEDED_BOTH: "Excede tiempo y km",
  }
  const ok = status === "COMPLIANT"
  return `<span class="audit-badge ${ok ? "audit-badge-high" : "audit-badge-review"}">${ok ? "✓" : "△"} ${auditEscape(labels[status] || status)}</span>`
}

function originalPhotoUrl(record) {
  return record.originalUrl || (record.documentId ? `/api/documents/${record.documentId}/file` : null)
}

function reviewEvidenceMarkup(record) {
  if (record.reviewStatus !== "review") return ""
  return `<div class="audit-review-evidence">
    ${record.dateCropUrl ? `<figure><button class="audit-crop-button" data-action="review" type="button" aria-label="Abrir foto completa para revisar la fecha"><img src="${auditEscape(record.dateCropUrl)}" alt="Recorte de la fecha"></button><figcaption>Fecha detectada: ${auditEscape(record.date || "—")}</figcaption></figure>` : ""}
    ${record.mileageCropUrl ? `<figure><button class="audit-crop-button" data-action="review" type="button" aria-label="Abrir foto completa para revisar el kilometraje"><img src="${auditEscape(record.mileageCropUrl)}" alt="Recorte del kilometraje"></button><figcaption>Kilometraje detectado: ${auditEscape(record.mileage === "" ? "—" : record.mileage)}</figcaption></figure>` : ""}
    <div class="audit-review-actions">${originalPhotoUrl(record) ? `<button class="button button-secondary" data-action="review" type="button">Ver ${record.sourceType === "pdf" ? "PDF" : "foto"} y corregir</button>` : '<button class="button button-secondary" data-action="correct" type="button">Corregir datos</button>'}
    ${record.eventId && auditDate(record.date) && record.mileage !== "" ? '<button class="button button-secondary" data-action="confirm" type="button">Confirmar lectura</button>' : ""}
    <small>Abre el original para comprobar y guardar ambos datos.</small></div>
  </div>`
}

function openPhotoReview(record) {
  const url = originalPhotoUrl(record)
  if (!url || auditState.saving) return
  reviewRecordId = record.id
  reviewZoom = 1
  reviewImage.style.width = "100%"
  reviewImage.style.height = "100%"
  const viewer = reviewImage.parentElement
  viewer.scrollTop = 0
  viewer.scrollLeft = 0
  document.querySelector("#audit-review-zoom").textContent = "100%"
  const isPdf = record.sourceType === "pdf"
  reviewImage.hidden = isPdf
  reviewPdf.hidden = !isPdf
  if (isPdf) reviewPdf.src = `${url}#page=${Math.max(1, Number(record.sourcePage) || 1)}`
  else reviewImage.src = url
  document.querySelector("#audit-review-title").textContent = `Revisar ${isPdf ? "PDF" : "foto"} de mantenimiento`
  document.querySelector("#audit-review-format").textContent = isPdf ? "PDF completo" : "Foto completa"
  reviewDialog.querySelectorAll("[data-zoom], #audit-review-zoom").forEach((control) => { control.hidden = isPdf })
  reviewImage.alt = `Comprobante completo: ${record.sourceFileName}`
  document.querySelector("#audit-review-filename").textContent = record.sourceFileName
  document.querySelector("#audit-review-original").href = url
  document.querySelector("#audit-review-demo").hidden = !record.id.startsWith("demo-")
  reviewDate.value = record.date || ""
  reviewMileage.value = record.mileage ?? ""
  reviewNotice.textContent = ""
  reviewDialog.showModal()
}

async function saveAuditCorrection(record, changes) {
  if (!record.eventId && record.documentId) {
    const event = await MaintenanceAnalysisAPI.createRecord(record.documentId, changes)
    Object.assign(record, MaintenanceAnalysisAPI.toMaintenanceRecords({ service_events: [event] }, record.sourceFileName)[0])
    if (!auditState.records.includes(record)) auditState.records.push(record)
    auditState.showingResults = true
    const file = auditState.files.find((item) => item.documentId === record.documentId)
    if (file) {
      const fileRecords = auditState.records.filter((item) => item.documentId === file.documentId)
      file.status = "completed"
      file.eventCount = fileRecords.length
      file.requiresReview = fileRecords.some((item) => item.reviewStatus === "review")
      auditState.failedItems = auditState.failedItems.filter((item) => item.id !== file.id)
      retryButton.classList.toggle("hidden", auditState.failedItems.length === 0)
    }
    try {
      await refreshBackendHistory()
      showPartialError("")
    } catch (error) {
      auditState.history = null
      showPartialError(`El mantenimiento se guardó, pero falta recalcular el historial: ${error.message}`)
    }
    updateAnalyzeAvailability()
    return
  }
  if (record.eventId) {
    Object.assign(record, await MaintenanceAnalysisAPI.updateRecord(record, changes))
    try {
      await refreshBackendHistory()
      showPartialError("")
    } catch (error) {
      auditState.history = null
      showPartialError(`La corrección se guardó, pero no fue posible recalcular el historial: ${error.message}`)
    }
  } else {
    if ("service_date" in changes) record.date = changes.service_date || ""
    if ("mileage_km" in changes) record.mileage = changes.mileage_km ?? ""
    const complete = auditDate(record.date) && record.mileage !== "" && Number.isFinite(Number(record.mileage)) && Number(record.mileage) >= 0
    record.manuallyVerified = Boolean(complete)
    record.reviewStatus = complete ? "manual" : "review"
  }
}

function intervalMarkup(interval, index) {
  const dateText = interval.withinDate ? `${interval.deltaDays} días · cumple` : `${interval.deltaDays} días · excede por ${interval.excessDays}`
  const mileageText = interval.withinMileage ? `${interval.deltaMileage >= 0 ? "+" : ""}${formatKm(interval.deltaMileage)} · cumple` : `${interval.deltaMileage >= 0 ? "+" : ""}${formatKm(interval.deltaMileage)} · excede por ${formatKm(interval.excessMileage)}`
  return `<article class="audit-interval">
    <div class="audit-interval-heading"><strong>Mantenimiento #${String(index + 1).padStart(2, "0")} → #${String(index + 2).padStart(2, "0")}</strong><span>${formatDate(interval.from.date)} → ${formatDate(interval.to.date)}</span></div>
    <div class="audit-interval-badges"><span class="${interval.withinDate ? "ok" : "bad"}">${dateText}</span><span class="${interval.withinMileage ? "ok" : "bad"}">${mileageText}</span></div>
    <p>${interval.message}</p>
  </article>`
}

function backendIntervalMarkup(row, index) {
  const status = row.validation.status
  const ok = status === "COMPLIANT" || status === "TOLERANCE_PERIOD"
  return `<article class="audit-interval">
    <div class="audit-interval-heading"><strong>Servicio #${String(index + 1).padStart(2, "0")}</strong><span>${formatDate(row.service_event.service_date)}</span></div>
    <div class="audit-interval-badges"><span class="${ok ? "ok" : "bad"}">${auditEscape(status)}</span>${row.delta_km == null ? "" : `<span class="${row.delta_km >= 0 ? "ok" : "bad"}">${row.delta_km >= 0 ? "+" : ""}${formatKm(row.delta_km)}</span>`}</div>
    <p>${auditEscape(row.validation.message)}</p>
  </article>`
}

function contractReferenceMarkup() {
  const vehicle = auditState.vehicles.find((item) => String(item.id) === vehicleSelect.value)
  const baseline = auditState.demo ? {} : auditState.history?.baseline || {
    date: vehicle?.fecha_inicio_contrato,
    mileage_km: vehicle?.initial_odometer,
  }
  const date = auditDate(baseline.date)
  const mileage = baseline.mileage_km
  return `<div class="audit-record-row audit-contract-reference" role="row" aria-label="Referencia inicial del contrato">
    <span role="cell"><small>Inicio de contrato</small><strong>${date ? formatDate(baseline.date) : "Sin fecha registrada"}</strong></span>
    <span role="cell"><small>Kilometraje inicial</small><strong>${mileage == null ? "Sin kilometraje registrado" : formatKm(mileage)}</strong></span>
    <span role="cell">Punto de partida</span>
    <span role="cell">—</span>
    <span role="cell"><span class="audit-badge audit-badge-reference">Referencia inicial</span></span>
    <span class="audit-source" role="cell">Contrato</span>
    <span role="cell">—</span>
  </div>`
}

function renderResults() {
  const analysis = calculateAudit(auditState.records)
  auditState.records = analysis.records
  const pending = analysis.records.some((record) => record.reviewStatus === "review")
  const rows = analysis.records.map((record, index) => `<div class="audit-record-row" role="row" data-record-id="${auditEscape(record.id)}">
    <label class="audit-record-field" role="cell"><span>Fecha</span><input data-field="date" type="date" value="${auditEscape(record.date)}" aria-label="Fecha del mantenimiento ${index + 1}"></label>
    <label class="audit-record-field audit-mileage" role="cell"><span>Kilometraje</span><span><input data-field="mileage" type="number" min="0" step="1" value="${auditEscape(record.mileage)}" aria-label="Kilometraje del mantenimiento ${index + 1}"><small>km</small></span></label>
    <span role="cell">${auditEscape(record.elapsedTime || "—")}</span>
    <span role="cell">${record.deltaKm == null ? "—" : `${record.deltaKm >= 0 ? "+" : ""}${formatKm(record.deltaKm)}`}</span>
    <span role="cell">${resultMarkup(record)}</span>
    <span class="audit-source" role="cell"><span>${record.sourceType === "manual" ? "Manual" : record.sourceType === "pdf" ? "PDF" : "Imagen"}</span>${originalPhotoUrl(record) ? `<button class="audit-text-button" data-action="review" type="button" title="Abrir ${auditEscape(record.sourceFileName)}">Ver ${record.sourceType === "pdf" ? "PDF" : "foto"}</button>` : ""}<small title="${auditEscape(record.sourceFileName)}">${auditEscape(record.sourceFileName)}</small></span>
    <span role="cell"><button class="audit-delete" data-action="delete" type="button" aria-label="Eliminar registro ${index + 1}">⌫</button></span>
    ${reviewEvidenceMarkup(record)}
  </div>`).join("")
  const backendResult = auditState.history?.summary?.result
  const needsReview = backendResult ? backendResult !== "COMPLIANT" : analysis.overallStatus === "review"
  const verdictTitle = backendResult === "COMPLIANT" ? "Historial dentro de las reglas" : backendResult === "TOLERANCE_PERIOD" ? "Historial dentro de tolerancia" : "Se necesita revisión manual"
  const verdictMessage = auditState.history?.summary?.message || (needsReview ? "Hay datos por confirmar, incompletos o inconsistentes. Revísalos antes de emitir el dictamen." : "Todos los registros están verificados y los intervalos cumplen los límites configurados.")
  const intervals = auditState.history
    ? auditState.history.rows.map(backendIntervalMarkup).join("")
    : analysis.chronologicalIntervals.map(intervalMarkup).join("")
  resultPanel.innerHTML = `${auditState.demo ? '<p class="example-notice">Ejemplo de demostración. Estos datos son ficticios y no se guardan en el historial.</p>' : ""}<section class="audit-history" aria-labelledby="audit-history-title">
    <div class="audit-section-heading"><div><h2 id="audit-history-title">Historial ordenado</h2><p>${analysis.records.length} mantenimientos · del más antiguo al más reciente</p><p>${auditState.demo ? "Este ejemplo no incluye datos de contrato." : "El primer intervalo se cuenta desde la fecha y el kilometraje de inicio del contrato."}</p></div>${pending ? '<span class="audit-badge audit-badge-review">△ Revisión pendiente</span>' : ""}</div>
    <div class="audit-record-table" role="table" aria-label="Historial de mantenimiento editable"><div class="audit-record-row audit-record-head" role="row"><span role="columnheader">Fecha</span><span role="columnheader">Kilometraje</span><span role="columnheader">Tiempo desde anterior</span><span role="columnheader">Km desde anterior</span><span role="columnheader">Resultado</span><span role="columnheader">Documento</span><span role="columnheader">Acción</span></div>${auditState.demo ? "" : contractReferenceMarkup()}${rows}</div>
    <div class="audit-record-footer"><p>Editar fecha o kilometraje marca el registro como verificado manualmente.</p><button class="audit-text-button" data-action="add" type="button">＋ Agregar registro</button></div>
  </section>
  <section class="audit-verdict ${needsReview ? "needs-review" : "compliant"}" aria-labelledby="audit-verdict-title">
    <div class="audit-verdict-heading"><span aria-hidden="true">${needsReview ? "△" : "✓"}</span><div><h2 id="audit-verdict-title">${verdictTitle}</h2><p>${auditEscape(verdictMessage)}</p></div></div>
    <div class="audit-interval-list">${intervals || '<p class="audit-no-intervals">Agrega al menos un registro completo para validar el historial.</p>'}</div>
  </section>`
  newAction.classList.remove("hidden")
}

async function refreshBackendHistory() {
  const eventIds = auditState.records.map((record) => record.eventId).filter(Boolean)
  if (!eventIds.length || !vehicleSelect.value) {
    auditState.history = null
    return
  }
  auditState.history = await MaintenanceAnalysisAPI.validateHistory(Number(vehicleSelect.value), eventIds)
  const historyRows = auditState.history?.rows || []
  const rows = new Map(historyRows.map((row) => [row.service_event.id, row]))
  const known = new Set(auditState.records.map((record) => record.eventId).filter(Boolean))
  historyRows.forEach((row) => {
    const event = row.service_event
    if (known.has(event.id)) return
    const evidence = event.field_evidence || {}
    const name = row.document.original_filename || `Documento #${event.document_id}`
    auditState.records.push({
      id: `event-${event.id}`, eventId: event.id, documentId: event.document_id,
      sourcePage: event.source_page,
      date: event.service_date || "", mileage: event.mileage_km ?? "", sourceFileName: name,
      sourceType: name.toLowerCase().endsWith(".pdf") ? "pdf" : "image",
      confidence: Number(evidence.combined_confidence ?? 0),
      reviewStatus: event.requires_human_review ? "review" : event.user_confirmed ? "manual" : "confirmed",
      manuallyVerified: event.user_confirmed, fieldEvidence: evidence, warnings: event.warnings || [],
      dateCropUrl: evidence.date?.crop_id ? `/api/service-events/${event.id}/evidence/date` : null,
      mileageCropUrl: evidence.mileage_km?.crop_id ? `/api/service-events/${event.id}/evidence/mileage_km` : null,
      originalUrl: `/api/documents/${event.document_id}/file`,
    })
  })
  auditState.records.forEach((record) => {
    const row = rows.get(record.eventId)
    if (!row) return
    record.elapsedTime = row.elapsed_time
    record.deltaKm = row.delta_km
    record.validationStatus = row.validation.status
    record.validationMessage = row.validation.message
    record.sourceFileName = row.document.original_filename || record.sourceFileName
    record.reviewStatus = row.service_event.requires_human_review ? "review" : row.service_event.user_confirmed ? "manual" : record.reviewStatus
    record.manuallyVerified = row.service_event.user_confirmed
  })
}

async function processImages(items = auditState.files, { retry = false } = {}) {
  if (!vehicleSelect.value) {
    showPartialError("Selecciona el vehículo al que pertenecen los documentos.")
    vehicleSelect.focus()
    return
  }
  if (!items.length) {
    showPartialError("Selecciona al menos un documento para analizar.")
    return
  }
  if (auditState.controller) return

  auditState.demo = false
  showPartialError("")
  retryButton.classList.add("hidden")
  processStatus.classList.remove("hidden")
  progressElement.max = items.length
  progressElement.value = 0
  progressLabel.textContent = `Procesando 0 de ${items.length}`
  currentFile.textContent = "Preparando archivos…"
  const controller = new AbortController()
  const runId = crypto.randomUUID()
  auditState.controller = controller
  auditState.runId = runId
  if (!retry) auditState.records = auditState.records.filter((record) => record.sourceType === "manual")
  items.forEach((item) => { item.status = "pending"; item.reprocess = Boolean(retry && item.documentId) })
  renderPreviews()
  updateAnalyzeAvailability()

  const settled = await MaintenanceAnalysisAPI.analyzeFiles({
    vehicleId: Number(vehicleSelect.value),
    items,
    signal: controller.signal,
    concurrency: 2,
    onProgress: ({ phase, completed, total, fileName, itemId }) => {
      if (auditState.runId !== runId) return
      const item = auditState.files.find((candidate) => candidate.id === itemId)
      if (item && phase === "processing") item.status = "processing"
      progressElement.max = total
      progressElement.value = completed
      progressLabel.textContent = `Procesando ${completed} de ${total}`
      currentFile.textContent = phase === "processing" ? `Analizando: ${fileName}` : `Finalizado: ${fileName}`
      renderPreviews()
    },
  })

  if (auditState.runId !== runId) return

  const failures = []
  const newRecords = []
  for (const outcome of settled) {
    if (outcome.status === "fulfilled") {
      const item = auditState.files.find((candidate) => candidate.id === outcome.value.itemId)
      if (item) {
        item.documentId = outcome.value.documentId
        item.status = "completed"
        item.eventCount = outcome.value.records.length
        item.requiresReview = outcome.value.records.some((record) => record.reviewStatus === "review")
      }
      if (outcome.value.records.length) newRecords.push(...outcome.value.records)
      else {
        if (item) item.status = "unreadable"
        failures.push({ item, reason: new Error("La foto se procesó, pero no se pudieron leer sus datos. Usa Ver foto y agregar mantenimiento para capturarlos.") })
      }
    } else {
      const item = auditState.files.find((candidate) => candidate.id === outcome.reason.itemId)
      if (item) {
        item.documentId = outcome.reason.documentId || item.documentId
        item.status = outcome.reason.name === "AbortError" ? "pending" : "failed"
      }
      if (outcome.reason.name !== "AbortError") failures.push({ item, reason: outcome.reason })
    }
  }

  const wasCancelled = controller.signal.aborted
  auditState.controller = null
  auditState.failedItems = failures.map((failure) => failure.item).filter(Boolean)
  const recordsById = new Map(auditState.records.map((record) => [record.id, record]))
  newRecords.forEach((record) => recordsById.set(record.id, record))
  auditState.records = [...recordsById.values()]
  renderPreviews()
  updateAnalyzeAvailability()
  processStatus.classList.add("hidden")

  if (auditState.records.length) {
    auditState.showingResults = true
    try {
      await refreshBackendHistory()
    } catch (error) {
      showPartialError(`Los eventos se guardaron, pero no fue posible recalcular VAL-002: ${error.message}`)
    }
    renderResults()
  }
  if (wasCancelled) {
    showPartialError("El análisis fue cancelado. Los archivos pendientes no se procesaron.")
  } else if (failures.length) {
    const details = [...new Set(failures.map((failure) => failure.reason.message))].join(" · ")
    showPartialError(`${failures.length} de ${items.length} archivos necesitan atención. ${details}`)
    retryButton.classList.remove("hidden")
  } else if (!auditState.records.length) {
    showPartialError("El análisis terminó, pero no produjo registros. Revisa el documento o agrega los datos manualmente.")
  }
}

function resetAudit() {
  auditState.controller?.abort()
  auditState.runId = crypto.randomUUID()
  auditState.files.forEach((item) => URL.revokeObjectURL(item.previewUrl))
  auditState.files = []
  auditState.records = []
  auditState.history = null
  auditState.showingResults = false
  auditState.controller = null
  auditState.failedItems = []
  auditState.demo = false
  uploadError.textContent = ""
  dropzone.classList.remove("has-error")
  renderPreviews()
  resultPanel.innerHTML = document.querySelector("#image-empty-template").innerHTML
  newAction.classList.add("hidden")
  retryButton.classList.add("hidden")
  processStatus.classList.add("hidden")
  showPartialError("")
  updateAnalyzeAvailability()
  const expand = document.querySelector("#validator-expand-table")
  if (expand.getAttribute("aria-expanded") === "true") expand.click()
  document.querySelector("#new-analysis").scrollIntoView({ behavior: "smooth" })
}

document.querySelector("#audit-file-trigger").addEventListener("click", () => fileInput.click())
fileInput.addEventListener("change", (event) => receiveFiles(event.target.files))
dropzone.addEventListener("dragenter", (event) => { event.preventDefault(); dropzone.classList.add("is-dragging") })
dropzone.addEventListener("dragover", (event) => event.preventDefault())
dropzone.addEventListener("dragleave", (event) => { if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("is-dragging") })
dropzone.addEventListener("drop", (event) => { event.preventDefault(); dropzone.classList.remove("is-dragging"); receiveFiles(event.dataTransfer.files) })
previewsPanel.addEventListener("click", (event) => {
  const remove = event.target.closest("button[data-file-id]")
  if (!remove) return
  const item = auditState.files.find((file) => file.id === remove.dataset.fileId)
  if (item) URL.revokeObjectURL(item.previewUrl)
  auditState.files = auditState.files.filter((file) => file.id !== remove.dataset.fileId)
  auditState.failedItems = auditState.failedItems.filter((file) => file.id !== remove.dataset.fileId)
  uploadError.textContent = ""
  dropzone.classList.remove("has-error")
  renderPreviews()
  retryButton.classList.toggle("hidden", auditState.failedItems.length === 0)
  updateAnalyzeAvailability()
})
exampleButton.addEventListener("click", () => {
  auditState.controller?.abort()
  auditState.controller = null
  auditState.runId = crypto.randomUUID()
  processStatus.classList.add("hidden")
  updateAnalyzeAvailability()
  auditState.records = AUDIT_DEMO_RECORDS.map((record) => ({ ...record, originalUrl: "/css/assets/maintenance-review-demo.svg" }))
  auditState.history = null
  auditState.demo = true
  auditState.showingResults = true
  showPartialError("")
  renderResults()
})
resultPanel.addEventListener("change", async (event) => {
  const input = event.target.closest("input[data-field]")
  if (!input || auditState.saving || !input.reportValidity()) return
  const row = input.closest("[data-record-id]")
  const record = auditState.records.find((item) => item.id === row.dataset.recordId)
  if (!record) return
  const field = input.dataset.field
  const value = field === "mileage" ? (input.value === "" ? "" : Number(input.value)) : input.value
  auditState.saving = true
  resultPanel.querySelectorAll("input, button").forEach((element) => { element.disabled = true })
  try {
    await saveAuditCorrection(
      record,
      field === "mileage" ? { mileage_km: value === "" ? null : value } : { service_date: value || null },
    )
  } catch (error) {
    showPartialError(`No fue posible guardar la corrección: ${error.message}`)
  }
  auditState.saving = false
  renderResults()
})
resultPanel.addEventListener("click", async (event) => {
  const action = event.target.closest("button[data-action]")
  if (!action || auditState.saving) return
  if (action.dataset.action === "review") {
    const record = auditState.records.find((item) => item.id === action.closest("[data-record-id]").dataset.recordId)
    if (record) openPhotoReview(record)
    return
  }
  if (action.dataset.action === "correct") {
    action.closest("[data-record-id]").querySelector('input[data-field="date"]')?.focus()
    return
  }
  if (action.dataset.action === "confirm") {
    const id = action.closest("[data-record-id]").dataset.recordId
    const record = auditState.records.find((item) => item.id === id)
    if (!record) return
    auditState.saving = true
    resultPanel.querySelectorAll("input, button").forEach((element) => { element.disabled = true })
    try {
      Object.assign(record, await MaintenanceAnalysisAPI.confirmRecord(record))
      await refreshBackendHistory()
      showPartialError("")
    } catch (error) {
      showPartialError(`No fue posible confirmar la lectura: ${error.message}`)
    }
    auditState.saving = false
    renderResults()
    return
  }
  if (action.dataset.action === "delete") {
    const id = action.closest("[data-record-id]").dataset.recordId
    auditState.records = auditState.records.filter((record) => record.id !== id)
  }
  if (action.dataset.action === "add") auditState.records.push({ id: `manual-${crypto.randomUUID()}`, date: "", mileage: "", sourceFileName: "Captura manual", sourceType: "manual", confidence: null, reviewStatus: "manual", manuallyVerified: true })
  renderResults()
})
reviewDialog.querySelector("#audit-review-close").addEventListener("click", () => reviewDialog.close())
reviewDialog.addEventListener("close", () => { reviewRecordId = null; reviewDraftRecord = null })
reviewImage.addEventListener("error", () => {
  reviewNotice.textContent = "No fue posible mostrar la foto. Prueba el enlace Abrir original."
})
reviewDialog.querySelectorAll("[data-zoom]").forEach((button) => button.addEventListener("click", () => {
  reviewZoom = Math.min(4, Math.max(1, reviewZoom + Number(button.dataset.zoom)))
  reviewImage.style.width = `${reviewZoom * 100}%`
  reviewImage.style.height = `${reviewZoom * 100}%`
  document.querySelector("#audit-review-zoom").textContent = `${Math.round(reviewZoom * 100)}%`
}))
reviewForm.addEventListener("submit", async (event) => {
  event.preventDefault()
  if (auditState.saving || !reviewForm.reportValidity()) return
  const record = auditState.records.find((item) => item.id === reviewRecordId) || reviewDraftRecord
  if (!record) return
  auditState.saving = true
  const submit = reviewForm.querySelector('[type="submit"]')
  submit.disabled = true
  submit.textContent = "Guardando…"
  reviewNotice.textContent = ""
  try {
    await saveAuditCorrection(record, { service_date: reviewDate.value, mileage_km: Number(reviewMileage.value) })
    renderResults()
    reviewDialog.close()
  } catch (error) {
    reviewNotice.textContent = `No fue posible guardar la corrección: ${error.message}`
  } finally {
    auditState.saving = false
    submit.disabled = false
    submit.textContent = "Guardar y recalcular"
    renderPreviews()
  }
})
document.querySelector("#audit-reset").addEventListener("click", resetAudit)
vehicleSelect.addEventListener("change", () => {
  updateAnalyzeAvailability()
  if (auditState.showingResults) renderResults()
})
analyzeButton.addEventListener("click", () => processImages())
retryButton.addEventListener("click", () => processImages(auditState.failedItems, { retry: true }))
document.querySelector("#audit-cancel").addEventListener("click", () => {
  if (!auditState.controller) return
  progressLabel.textContent = "Cancelando…"
  auditState.controller.abort()
})
UI.vehicleOptions(vehicleSelect).then((vehicles) => {
  auditState.vehicles = vehicles
  updateAnalyzeAvailability()
}).catch((error) => {
  vehicleSelect.innerHTML = '<option value="">No fue posible cargar vehículos</option>'
  showPartialError(error.message)
})
window.addEventListener("beforeunload", () => auditState.files.forEach((item) => URL.revokeObjectURL(item.previewUrl)))
