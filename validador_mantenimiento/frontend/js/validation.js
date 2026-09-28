function resultSymbol(status) {
  return {
    APROBADO: "✓", RECHAZADO: "×", FUERA_DE_VENTANA: "○", REVISION_REQUERIDA: "!",
    COMPLIANT: "✓", TOLERANCE_PERIOD: "!", EXCEEDED_MILEAGE: "×", EXCEEDED_TIME: "×", EXCEEDED_BOTH: "×",
    FIRST_MAINTENANCE_AVAILABLE: "✓", REQUIRES_REVIEW: "!", INSUFFICIENT_DATA: "!", NOT_MAINTENANCE_EVENT: "○",
  }[status] || "?"
}

function statusLabel(status) {
  return {
    FIRST_MAINTENANCE_AVAILABLE: "Primer mantenimiento disponible",
    COMPLIANT: "Cumple intervalo",
    TOLERANCE_PERIOD: "Periodo de tolerancia",
    EXCEEDED_MILEAGE: "Excede kilometraje",
    EXCEEDED_TIME: "Excede tiempo",
    EXCEEDED_BOTH: "Excede ambos límites",
    REQUIRES_REVIEW: "Requiere revisión",
    INSUFFICIENT_DATA: "Datos insuficientes",
    NOT_MAINTENANCE_EVENT: "Evento no reinicia intervalo",
  }[status] || status.replaceAll("_", " ")
}

function ruleMarkup(title, met, detail) {
  return `<article class="rule ${met === true ? "rule-ok" : met === false ? "rule-waiting" : ""}"><h3>${met === true ? "✓" : met === false ? "×" : "—"} ${title}</h3><p>${detail}</p></article>`
}

function resultMarkup(item, includeAudit = false) {
  const isInterval = ["COMPLIANT", "TOLERANCE_PERIOD", "EXCEEDED_MILEAGE", "EXCEEDED_TIME", "EXCEEDED_BOTH"].includes(item.status)
  const details = item.analysis_details || {}
  const policy = details.policy || {}
  const maximum = details.maximum_limit || policy
  const policyKm = Number(maximum.kilometers)
  const policyMonths = Number(maximum.months)
  const kilometerLabel = Number.isFinite(policyKm) ? `${policyKm.toLocaleString("es-MX")} km máximos` : "kilometraje"
  const timeLabel = Number.isFinite(policyMonths) ? `${policyMonths} meses máximos` : "tiempo"
  const kilometerDetail = item.meets_kilometer_condition === null ? "No aplica para este resultado." : item.meets_kilometer_condition ? "El intervalo se mantuvo dentro del límite." : `El límite de ${kilometerLabel} fue excedido.`
  const timeDetail = item.meets_time_condition === null ? "No aplica para este resultado." : item.meets_time_condition ? "El intervalo se mantuvo dentro del límite." : `El límite de ${timeLabel} fue excedido.`
  const rules = isInterval ? `<div class="rule-grid">${ruleMarkup(`Regla de ${kilometerLabel}`, item.meets_kilometer_condition, kilometerDetail)}${ruleMarkup(`Regla de ${timeLabel}`, item.meets_time_condition, timeDetail)}</div>` : ""
  const delta = item.analysis_details?.delta_km
  const condition = details.vehicle_condition === "new" ? "Nuevo (M1)" : details.vehicle_condition === "used" ? "Seminuevo (M2)" : "Sin definir"
  const sequence = details.service_sequence === "first" ? "Primer servicio" : details.service_sequence === "subsequent" ? "Servicio posterior" : "Por revisar"
  const policyName = policy.source === "manufacturer" ? `Nuevo (M1) · ${policy.brand || "marca"}` : policy.source === "used_vehicle_default" ? "Seminuevo (M2) · regla fija" : "Sin política aplicable"
  return `<section class="card result result-${UI.escape(item.status)}"><div class="result-heading"><span class="result-symbol">${resultSymbol(item.status)}</span><div><p class="eyebrow">Validación</p><h2>${UI.escape(statusLabel(item.status))}</h2><p>${UI.escape(item.message)}</p></div></div><div class="details-grid"><div class="detail"><span>Condición</span><strong>${UI.escape(condition)}</strong></div><div class="detail"><span>Secuencia</span><strong>${UI.escape(sequence)}</strong></div><div class="detail"><span>Política aplicada</span><strong>${UI.escape(policyName)}</strong></div><div class="detail"><span>Fecha del servicio</span><strong>${UI.date(item.document_date)}</strong></div><div class="detail"><span>Kilometraje registrado</span><strong>${UI.km(item.document_odometer)}</strong></div><div class="detail"><span>Referencia</span><strong>${UI.date(item.last_maintenance_date)} · ${UI.km(item.last_maintenance_odometer)}</strong></div><div class="detail"><span>Límite por kilometraje</span><strong>${UI.km(item.kilometer_limit)}</strong></div><div class="detail"><span>Límite por fecha</span><strong>${UI.date(item.date_limit)}</strong></div><div class="detail"><span>Delta de kilometraje</span><strong>${UI.km(delta)}</strong></div></div>${rules}${item.reasons?.length ? `<ul class="reason-list">${item.reasons.map((reason) => `<li>${UI.escape(reason)}</li>`).join("")}</ul>` : ""}${includeAudit ? `<h3 class="card-title" style="margin-top:22px">Trazabilidad</h3><ol class="timeline">${item.audit_logs?.map((event) => `<li><span class="event-time">${UI.dateTime(event.created_at)} · ${UI.escape(event.event_type)}</span>${UI.escape(event.message)}</li>`).join("") || "<li>Sin eventos registrados.</li>"}</ol>` : ""}</section>`
}
window.resultMarkup = resultMarkup

function historyStatus(status) {
  if (status === "COMPLIANT") return { label: "EN REGLA", tone: "ok" }
  if (status === "TOLERANCE_PERIOD") return { label: "PERIODO DE TOLERANCIA", tone: "tolerance" }
  if (status === "EXCEEDED_MILEAGE") return { label: "FUERA POR KILOMETRAJE", tone: "bad" }
  if (status === "EXCEEDED_TIME") return { label: "FUERA POR TIEMPO", tone: "bad" }
  if (status === "EXCEEDED_BOTH") return { label: "FUERA POR AMBOS", tone: "bad" }
  return { label: "REVISIÓN HUMANA", tone: "review" }
}

function policyLabel(history) {
  const policy = history.policy
  if (!policy) return history.policy_reasons?.join(" · ") || "Sin política aplicable"
  const name = policy.source === "manufacturer" ? `NUEVO (M1) · ${policy.brand}` : "SEMINUEVO (M2)"
  return `${name} — ${policy.months} meses / ${Number(policy.kilometers).toLocaleString("es-MX")} km`
}

function signedKilometers(value) {
  if (value === null || value === undefined) return "—"
  const sign = Number(value) < 0 ? "−" : ""
  return `${sign}${Math.abs(Number(value)).toLocaleString("es-MX")} km`
}

function documentLinkMarkup(row, pdfUrl = null) {
  const document = row.document || {}
  const href = pdfUrl || (document.id ? `/api/documents/${document.id}/file` : null)
  if (!href) return "—"
  return `<a class="history-document-link" href="${UI.escape(href)}" target="_blank" rel="noopener noreferrer" title="${UI.escape(document.original_filename || "Documento PDF")}">↗ Abrir documento PDF</a>`
}

function historyRowMarkup(row, pdfUrl = null) {
  const event = row.service_event
  const state = historyStatus(row.validation.status)
  return `<tr class="history-row history-row-${state.tone}">
    <td><strong>${UI.date(event.service_date)}</strong></td>
    <td>${UI.km(event.mileage_km)}</td>
    <td>${UI.escape(row.elapsed_time || "Por confirmar")}</td>
    <td>${signedKilometers(row.delta_km)}</td>
    <td><span class="history-status history-status-${state.tone}">${state.label}</span></td>
    <td>${documentLinkMarkup(row, pdfUrl)}</td>
  </tr>`
}

function renderHistory(history, pdfUrls = new Map()) {
  const result = document.querySelector("#history-result")
  const body = document.querySelector("#history-timeline-body")
  const baseline = history.baseline || {}
  document.querySelector("#history-policy-label").textContent = `Política aplicada: ${policyLabel(history)}`
  body.innerHTML = `<tr class="history-reference-row">
      <td><strong>${UI.date(baseline.date)}</strong></td>
      <td>${UI.km(baseline.mileage_km)}</td>
      <td>Inicio de contrato</td>
      <td>—</td>
      <td><span class="history-status history-status-reference">REFERENCIA</span></td>
      <td>—</td>
    </tr>${history.rows.map((row) => historyRowMarkup(row, pdfUrls.get(row.document?.id))).join("")}`

  const summary = history.summary
  const summaryTone = summary.result === "COMPLIANT" ? "ok" : summary.result === "TOLERANCE_PERIOD" ? "tolerance" : summary.result === "NON_COMPLIANT" ? "bad" : "review"
  const summaryLabel = summary.result === "COMPLIANT" ? "EN REGLA" : summary.result === "TOLERANCE_PERIOD" ? "CON TOLERANCIA" : summary.result === "NON_COMPLIANT" ? "CON INCUMPLIMIENTOS" : "REVISIÓN NECESARIA"
  document.querySelector("#history-summary").innerHTML = `<div class="history-summary-heading">
      <div><p class="eyebrow">Resultado del historial</p><h3>${UI.escape(summary.message)}</h3></div>
      <span class="history-status history-status-${summaryTone}">${summaryLabel}</span>
    </div>
    <p class="history-summary-policy"><strong>Política:</strong> ${UI.escape(policyLabel(history))}</p>
    ${history.ephemeral ? '<p class="history-summary-policy"><strong>Prueba temporal:</strong> los PDF y sus resultados no se guardaron en el historial del vehículo.</p>' : ""}
    <div class="history-summary-counts">
      <div><span>Servicios analizados</span><strong>${summary.services_analyzed}</strong></div>
      <div><span>En regla</span><strong>${summary.compliant}</strong></div>
      <div><span>En tolerancia</span><strong>${summary.tolerance_period}</strong></div>
      <div><span>Incumplimientos</span><strong>${summary.non_compliant}</strong></div>
      <div><span>Revisión humana</span><strong>${summary.requires_review}</strong></div>
    </div>`
  result.classList.remove("hidden")
  result.scrollIntoView({ behavior: "smooth", block: "start" })
}

const validationForm = document.querySelector("#validation-form")

if (validationForm) {
  const validationNotice = document.querySelector("#validation-notice")
  const vehicleSelect = document.querySelector("#validation-vehicle")
  const fileInput = document.querySelector("#document-files")
  const dropzone = document.querySelector("#history-dropzone")
  const fileList = document.querySelector("#selected-documents")
  const validateButton = document.querySelector("#validate-button")
  const progress = document.querySelector("#history-progress")
  const progressLabel = document.querySelector("#history-progress-label")
  const progressCount = document.querySelector("#history-progress-count")
  const progressBar = document.querySelector("#history-progress-bar")
  const historyResult = document.querySelector("#history-result")
  const state = { items: [], vehicleId: null, processing: false, history: null, pdfUrls: new Map() }

  function clearPdfUrls() {
    new Set(state.pdfUrls.values()).forEach((url) => URL.revokeObjectURL(url))
    state.pdfUrls.clear()
  }

  function fileKey(file) {
    return `${file.name}:${file.size}:${file.lastModified}`
  }

  function isPdf(file) {
    return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")
  }

  function syncAnalyzeButton() {
    validateButton.disabled = state.processing || !vehicleSelect.value || !state.items.length
  }

  function renderFiles() {
    if (!state.items.length) {
      fileList.className = "history-file-list empty-history-files"
      fileList.textContent = "Aún no has seleccionado documentos."
      syncAnalyzeButton()
      return
    }
    fileList.className = "history-file-list"
    fileList.innerHTML = state.items.map((item) => {
      const status = item.status === "analyzed" ? "Analizado" : item.status === "uploading" ? "Procesando…" : item.status === "error" ? "Error" : "Listo"
      const symbol = item.status === "analyzed" ? "✓" : item.status === "error" ? "!" : "•"
      const removable = ["pending", "error"].includes(item.status) && !state.processing
      return `<div class="history-file history-file-${item.status}">
        <span class="history-file-state">${symbol}</span>
        <div><strong>${UI.escape(item.file.name)}</strong><small>${(item.file.size / 1024 / 1024).toLocaleString("es-MX", { maximumFractionDigits: 2 })} MB · ${status}${item.error ? ` · ${UI.escape(item.error)}` : ""}</small></div>
        ${removable ? `<button type="button" data-remove-file="${UI.escape(item.key)}" aria-label="Retirar ${UI.escape(item.file.name)}">×</button>` : ""}
      </div>`
    }).join("")
    syncAnalyzeButton()
  }

  function addFiles(files) {
    clearPdfUrls()
    state.history = null
    const existing = new Set(state.items.map((item) => item.key))
    const rejected = []
    Array.from(files).forEach((file) => {
      const key = fileKey(file)
      if (!isPdf(file)) {
        rejected.push(file.name)
      } else if (!existing.has(key)) {
        state.items.push({ key, file, status: "pending", document: null, analysis: null, events: [], error: null })
        existing.add(key)
      }
    })
    if (rejected.length) UI.notice(validationNotice, `Sólo se admiten archivos PDF: ${rejected.join(", ")}.`, "error")
    else UI.clearNotice(validationNotice)
    historyResult.classList.add("hidden")
    renderFiles()
  }

  async function previewHistory() {
    const form = new FormData()
    form.append("vehicle_id", vehicleSelect.value)
    state.items.forEach((item) => {
      item.status = "uploading"
      item.error = null
      form.append("files", item.file)
    })
    renderFiles()
    state.history = await API.request("/history-validations/preview", {
      method: "POST",
      body: form,
    })
    clearPdfUrls()
    state.items.forEach((item, index) => {
      const pdfBlob = item.file.slice(0, item.file.size, "application/pdf")
      const documentId = state.history.preview_document_ids?.[index]
      if (documentId) state.pdfUrls.set(documentId, URL.createObjectURL(pdfBlob))
    })
    renderHistory(state.history, state.pdfUrls)
    progressBar.value = state.items.length
    state.items = []
    renderFiles()
    UI.notice(validationNotice, "Prueba terminada. Los PDF y el resultado no se guardaron; puedes cargarlos nuevamente.", "success")
  }

  fileInput.addEventListener("change", (event) => {
    addFiles(event.target.files)
    fileInput.value = ""
  })

  ;["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault()
    dropzone.classList.add("is-dragging")
  }))
  ;["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault()
    dropzone.classList.remove("is-dragging")
  }))
  dropzone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files))

  fileList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-remove-file]")
    if (!button) return
    state.items = state.items.filter((item) => item.key !== button.dataset.removeFile)
    renderFiles()
  })

  vehicleSelect.addEventListener("change", () => {
    const nextVehicleId = vehicleSelect.value || null
    if (state.vehicleId && state.vehicleId !== nextVehicleId) {
      clearPdfUrls()
      state.items = []
      state.history = null
      historyResult.classList.add("hidden")
      renderFiles()
      UI.notice(validationNotice, "Se limpió la selección porque cambió el vehículo.", "info")
    }
    state.vehicleId = nextVehicleId
    syncAnalyzeButton()
  })

  validationForm.addEventListener("submit", async (event) => {
    event.preventDefault()
    if (!vehicleSelect.value) {
      UI.notice(validationNotice, "Selecciona el vehículo del historial.", "error")
      return
    }
    if (!state.items.length) {
      UI.notice(validationNotice, "Agrega al menos un documento PDF.", "error")
      return
    }
    state.processing = true
    state.vehicleId = vehicleSelect.value
    syncAnalyzeButton()
    progress.classList.remove("hidden")
    progressBar.max = state.items.length || 1
    progressBar.value = 0
    progressLabel.textContent = `Analizando ${state.items.length} PDF${state.items.length === 1 ? "" : "s"}`
    progressCount.textContent = "Prueba temporal"
    UI.notice(validationNotice, "Analizando los documentos en modo temporal…", "info")
    try {
      await previewHistory()
    } catch (error) {
      state.items.forEach((item) => {
        item.status = "error"
        item.error = error.message
      })
      UI.notice(validationNotice, error.message, "error")
    } finally {
      state.processing = false
      progress.classList.add("hidden")
      syncAnalyzeButton()
      renderFiles()
    }
  })

  UI.vehicleOptions(vehicleSelect).then(syncAnalyzeButton).catch((error) => UI.notice(validationNotice, error.message, "error"))
  window.addEventListener("beforeunload", clearPdfUrls)
  renderFiles()
}
