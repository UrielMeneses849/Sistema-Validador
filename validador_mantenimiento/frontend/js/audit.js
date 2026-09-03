const AUDIT_LIMITS = Object.freeze({ maxFiles: 15, maxFileSize: 10 * 1024 * 1024, dateMonths: 6, mileageKm: 10000 })
const AUDIT_TYPES = new Set(["image/jpeg", "image/png", "image/webp"])

// Fixture de demostración: no procede de OCR ni se persiste en el backend.
const AUDIT_DEMO_RECORDS = Object.freeze([
  { id: "demo-01", date: "2023-01-24", mileage: 10750, sourceFileName: "libro-1.jpg", sourceType: "image", confidence: 0.96, reviewStatus: "confirmed", manuallyVerified: false, rawExtractedText: "24/01/2023 · 10,750 km" },
  { id: "demo-02", date: "2023-08-15", mileage: 30294, sourceFileName: "libro-2.jpg", sourceType: "image", confidence: 0.93, reviewStatus: "confirmed", manuallyVerified: false, rawExtractedText: "15/08/2023 · 30,294 km" },
  { id: "demo-03", date: "2024-01-06", mileage: 40123, sourceFileName: "sello-tenue.jpg", sourceType: "image", confidence: 0.74, reviewStatus: "review", manuallyVerified: false, rawExtractedText: "06/01/2024 · 40?23 km" },
])

const auditState = { files: [], records: [], showingResults: false, controller: null, failedItems: [], runId: null }
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

function auditEscape(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#039;", '"': "&quot;" })[character])
}

function auditDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return null
  const [year, month, day] = value.split("-").map(Number)
  const date = new Date(year, month - 1, day)
  return Number.isNaN(date.getTime()) ? null : date
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
  if (files.length > AUDIT_LIMITS.maxFiles) return `Puedes seleccionar hasta ${AUDIT_LIMITS.maxFiles} imágenes por análisis.`
  const invalid = files.find((file) => !AUDIT_TYPES.has(file.type))
  if (invalid) return `“${invalid.name}” no es JPG, PNG ni WEBP.`
  const oversized = files.find((file) => file.size > AUDIT_LIMITS.maxFileSize)
  if (oversized) return `“${oversized.name}” supera el límite de 10 MB.`
  return ""
}

function updateAnalyzeAvailability() {
  analyzeButton.disabled = !auditState.files.length || !vehicleSelect.value || Boolean(auditState.controller)
}

function showPartialError(message) {
  partialError.textContent = message
  partialError.classList.toggle("show", Boolean(message))
}

function receiveFiles(fileList) {
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
  heading.innerHTML = `<strong>${auditState.files.length} ${auditState.files.length === 1 ? "imagen" : "imágenes"}</strong><span>${completed ? `${completed} procesadas` : "Listas para OCR"}</span>`
  const list = document.createElement("div")
  list.className = "audit-preview-list"
  auditState.files.forEach((item) => {
    const preview = document.createElement("div")
    preview.className = "audit-preview"
    const image = document.createElement("img")
    image.src = item.previewUrl
    image.alt = `Vista previa de ${item.file.name}`
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
    state.textContent = { pending: "Pendiente", processing: "Procesando", completed: "Lista", failed: "Error" }[item.status] || "Pendiente"
    preview.append(image, remove, name, state)
    list.append(preview)
  })
  previewsPanel.append(heading, list)
}

function statusMarkup(record) {
  if (record.reviewStatus === "manual" || record.manuallyVerified) return '<span class="audit-badge audit-badge-manual">✓ Revisión manual</span>'
  if (record.reviewStatus === "review") return `<span class="audit-badge audit-badge-review">△ Revisar · ${Math.round((record.confidence || 0) * 100)}%</span>`
  return `<span class="audit-badge audit-badge-high">✓ Alta confianza · ${Math.round((record.confidence || 0) * 100)}%</span>`
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

function renderResults() {
  const analysis = calculateAudit(auditState.records)
  auditState.records = analysis.records
  const pending = analysis.records.some((record) => record.reviewStatus === "review")
  const rows = analysis.records.map((record, index) => `<div class="audit-record-row" role="row" data-record-id="${auditEscape(record.id)}">
    <span class="audit-record-number" role="cell">${String(index + 1).padStart(2, "0")}</span>
    <label class="audit-record-field" role="cell"><span>Fecha</span><input data-field="date" type="date" value="${auditEscape(record.date)}" aria-label="Fecha del mantenimiento ${index + 1}"></label>
    <label class="audit-record-field audit-mileage" role="cell"><span>Kilometraje</span><span><input data-field="mileage" type="number" min="0" step="1" value="${auditEscape(record.mileage)}" aria-label="Kilometraje del mantenimiento ${index + 1}"><small>km</small></span></label>
    <span class="audit-source" role="cell"><span>${record.sourceType === "manual" ? "Manual" : "Imagen"}</span><small title="${auditEscape(record.sourceFileName)}">${auditEscape(record.sourceFileName)}</small></span>
    <span role="cell">${statusMarkup(record)}</span>
    <span role="cell"><button class="audit-delete" data-action="delete" type="button" aria-label="Eliminar registro ${index + 1}">⌫</button></span>
  </div>`).join("")
  const needsReview = analysis.overallStatus === "review"
  resultPanel.innerHTML = `<section class="audit-history" aria-labelledby="audit-history-title">
    <div class="audit-section-heading"><div><h2 id="audit-history-title">Historial ordenado</h2><p>${analysis.records.length} mantenimientos · del más antiguo al más reciente</p></div>${pending ? '<span class="audit-badge audit-badge-review">△ Revisión pendiente</span>' : ""}</div>
    <div class="audit-record-table" role="table" aria-label="Historial de mantenimiento editable"><div class="audit-record-row audit-record-head" role="row"><span role="columnheader">#</span><span role="columnheader">Fecha</span><span role="columnheader">Kilometraje</span><span role="columnheader">Fuente</span><span role="columnheader">Estado</span><span role="columnheader">Acción</span></div>${rows}</div>
    <div class="audit-record-footer"><p>Editar fecha o kilometraje marca el registro como verificado manualmente.</p><button class="audit-text-button" data-action="add" type="button">＋ Agregar registro</button></div>
  </section>
  <section class="audit-verdict ${needsReview ? "needs-review" : "compliant"}" aria-labelledby="audit-verdict-title">
    <div class="audit-verdict-heading"><span aria-hidden="true">${needsReview ? "△" : "✓"}</span><div><h2 id="audit-verdict-title">${needsReview ? "Se necesita revisión manual" : "Historial dentro de las reglas"}</h2><p>${needsReview ? "Hay datos por confirmar, incompletos o inconsistentes. Revísalos antes de emitir el dictamen." : "Todos los registros están verificados y los intervalos cumplen los límites configurados."}</p></div></div>
    <div class="audit-interval-list">${analysis.chronologicalIntervals.map(intervalMarkup).join("") || '<p class="audit-no-intervals">Agrega al menos dos registros completos para comparar intervalos.</p>'}</div>
  </section>`
  newAction.classList.remove("hidden")
}

async function processImages(items = auditState.files, { retry = false } = {}) {
  if (!vehicleSelect.value) {
    showPartialError("Selecciona el vehículo al que pertenecen las imágenes.")
    vehicleSelect.focus()
    return
  }
  if (!items.length) {
    showPartialError("Selecciona al menos una imagen para analizar.")
    return
  }
  if (auditState.controller) return

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
  items.forEach((item) => { item.status = "pending" })
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
      }
      if (outcome.value.records.length) newRecords.push(...outcome.value.records)
      else {
        if (item) item.status = "failed"
        failures.push({ item, reason: new Error("No se detectaron registros de mantenimiento; revisa la imagen manualmente.") })
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
    renderResults()
  }
  if (wasCancelled) {
    showPartialError("El análisis fue cancelado. Los archivos pendientes no se procesaron.")
  } else if (failures.length) {
    const details = [...new Set(failures.map((failure) => failure.reason.message))].join(" · ")
    showPartialError(`${failures.length} de ${items.length} imágenes no se completaron. ${details}`)
    retryButton.classList.remove("hidden")
  } else if (!auditState.records.length) {
    showPartialError("El análisis terminó, pero no produjo registros. Revisa la imagen o agrega los datos manualmente.")
  }
}

function resetAudit() {
  auditState.controller?.abort()
  auditState.runId = crypto.randomUUID()
  auditState.files.forEach((item) => URL.revokeObjectURL(item.previewUrl))
  auditState.files = []
  auditState.records = []
  auditState.showingResults = false
  auditState.controller = null
  auditState.failedItems = []
  uploadError.textContent = ""
  dropzone.classList.remove("has-error")
  renderPreviews()
  resultPanel.innerHTML = '<div class="audit-empty"><span class="audit-empty-icon" aria-hidden="true">✓</span><h2>Aquí aparecerá el dictamen</h2><p>El historial mostrará fechas, kilometrajes, confianza de lectura y cada intervalo que exceda las reglas.</p></div>'
  newAction.classList.add("hidden")
  retryButton.classList.add("hidden")
  processStatus.classList.add("hidden")
  showPartialError("")
  updateAnalyzeAvailability()
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
  auditState.records = AUDIT_DEMO_RECORDS.map((record) => ({ ...record }))
  auditState.showingResults = true
  showPartialError("")
  renderResults()
})
resultPanel.addEventListener("change", async (event) => {
  const input = event.target.closest("input[data-field]")
  if (!input) return
  const row = input.closest("[data-record-id]")
  const record = auditState.records.find((item) => item.id === row.dataset.recordId)
  if (!record) return
  const field = input.dataset.field
  const value = field === "mileage" ? (input.value === "" ? "" : Number(input.value)) : input.value
  const previous = { ...record }
  record[field] = value
  if (!record.eventId) {
    record.manuallyVerified = true
    record.reviewStatus = "manual"
    renderResults()
    return
  }
  input.disabled = true
  try {
    const updated = await MaintenanceAnalysisAPI.updateRecord(
      record,
      field === "mileage" ? { mileage_km: value === "" ? null : value } : { service_date: value || null },
    )
    Object.assign(record, updated)
    showPartialError("")
  } catch (error) {
    Object.assign(record, previous)
    showPartialError(`No fue posible guardar la corrección: ${error.message}`)
  }
  renderResults()
})
resultPanel.addEventListener("click", (event) => {
  const action = event.target.closest("button[data-action]")
  if (!action) return
  if (action.dataset.action === "delete") {
    const id = action.closest("[data-record-id]").dataset.recordId
    auditState.records = auditState.records.filter((record) => record.id !== id)
  }
  if (action.dataset.action === "add") auditState.records.push({ id: `manual-${crypto.randomUUID()}`, date: "", mileage: "", sourceFileName: "Captura manual", sourceType: "manual", confidence: null, reviewStatus: "manual", manuallyVerified: true })
  renderResults()
})
document.querySelector("#audit-reset").addEventListener("click", resetAudit)
vehicleSelect.addEventListener("change", updateAnalyzeAvailability)
analyzeButton.addEventListener("click", () => processImages())
retryButton.addEventListener("click", () => processImages(auditState.failedItems, { retry: true }))
document.querySelector("#audit-cancel").addEventListener("click", () => {
  if (!auditState.controller) return
  progressLabel.textContent = "Cancelando…"
  auditState.controller.abort()
})
UI.vehicleOptions(vehicleSelect).then(updateAnalyzeAvailability).catch((error) => {
  vehicleSelect.innerHTML = '<option value="">No fue posible cargar vehículos</option>'
  showPartialError(error.message)
})
window.addEventListener("beforeunload", () => auditState.files.forEach((item) => URL.revokeObjectURL(item.previewUrl)))
