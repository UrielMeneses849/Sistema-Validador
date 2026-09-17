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

function extractedValue(fields, name) {
  return fields?.[name]?.normalized_value ?? fields?.[name]?.raw_value ?? null
}

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
  const sign = Number(value) >= 0 ? "+" : "−"
  return `${sign}${Math.abs(Number(value)).toLocaleString("es-MX")} km`
}

function documentDetailMarkup(row) {
  const document = row.document || {}
  const event = row.service_event || {}
  const fields = document.extracted_fields || {}
  const vin = extractedValue(fields, "vin") || "—"
  const plates = extractedValue(fields, "plates") || "—"
  const invoice = extractedValue(fields, "invoice_number") || event.repair_order_number || "—"
  const confidence = event.confidence === "high" ? "Alta" : event.confidence === "medium" ? "Media" : "Baja"
  const evidence = JSON.stringify(event.field_evidence || {}, null, 2)
  const correctable = event.requires_human_review
    || !event.service_date
    || event.mileage_km === null
    || row.validation?.reasons?.some((reason) => reason.includes("inconsistency") || reason.includes("anterior a la fecha"))
  const correction = correctable ? `<form class="history-correction" data-event-id="${event.id}">
      <h4>Confirmar o corregir datos</h4>
      <div class="history-correction-fields">
        <label>Fecha del servicio<input name="service_date" type="date" value="${UI.escape(event.service_date || "")}" required></label>
        <label>Kilometraje<input name="mileage_km" type="number" min="0" step="1" value="${event.mileage_km ?? ""}" required></label>
        <button class="button button-primary button-small" type="submit">Confirmar y recalcular</button>
      </div>
    </form>` : ""
  return `<details class="history-document-details">
    <summary>Ver detalles del documento</summary>
    <div class="history-document-grid">
      <div><span>Archivo</span><strong>${UI.escape(document.original_filename || "—")}</strong></div>
      <div><span>VIN</span><strong>${UI.escape(vin)}</strong></div>
      <div><span>Placas</span><strong>${UI.escape(plates)}</strong></div>
      <div><span>Proveedor</span><strong>${UI.escape(event.dealer || "—")}</strong></div>
      <div><span>Factura / orden</span><strong>${UI.escape(invoice)}</strong></div>
      <div><span>Método</span><strong>${UI.escape(document.extraction_method || event.extraction_method || "—")}</strong></div>
      <div><span>Confianza del evento</span><strong>${confidence}</strong></div>
      <div><span>Tipo documental</span><strong>${UI.escape(document.document_type || "—")}</strong></div>
      <div class="history-detail-full"><span>Servicio detectado</span><strong>${UI.escape(event.service_type || event.description || "—")}</strong></div>
      <div class="history-detail-full"><span>Descripción</span><strong>${UI.escape(event.description || "—")}</strong></div>
    </div>
    ${document.warnings?.length ? `<p class="history-document-warning">${document.warnings.map(UI.escape).join(" · ")}</p>` : ""}
    ${correction}
    <details class="history-raw-evidence"><summary>Ver texto y evidencia técnica</summary><p>${UI.escape(document.extracted_text || "Sin texto extraído.")}</p><pre>${UI.escape(evidence)}</pre></details>
  </details>`
}

function historyRowMarkup(row) {
  const event = row.service_event
  const state = historyStatus(row.validation.status)
  return `<tr class="history-row history-row-${state.tone}">
    <td><strong>${UI.date(event.service_date)}</strong></td>
    <td>${UI.km(event.mileage_km)}</td>
    <td>${UI.escape(row.elapsed_time || "Por confirmar")}</td>
    <td>${signedKilometers(row.delta_km)}</td>
    <td><span class="history-status history-status-${state.tone}">${state.label}</span></td>
    <td>${documentDetailMarkup(row)}</td>
  </tr>`
}

function renderHistory(history) {
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
    </tr>${history.rows.map(historyRowMarkup).join("")}`

  const summary = history.summary
  const summaryTone = summary.result === "COMPLIANT" ? "ok" : summary.result === "TOLERANCE_PERIOD" ? "tolerance" : summary.result === "NON_COMPLIANT" ? "bad" : "review"
  const summaryLabel = summary.result === "COMPLIANT" ? "EN REGLA" : summary.result === "TOLERANCE_PERIOD" ? "CON TOLERANCIA" : summary.result === "NON_COMPLIANT" ? "CON INCUMPLIMIENTOS" : "REVISIÓN NECESARIA"
  document.querySelector("#history-summary").innerHTML = `<div class="history-summary-heading">
      <div><p class="eyebrow">Resultado del historial</p><h3>${UI.escape(summary.message)}</h3></div>
      <span class="history-status history-status-${summaryTone}">${summaryLabel}</span>
    </div>
    <p class="history-summary-policy"><strong>Política:</strong> ${UI.escape(policyLabel(history))}</p>
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
  const state = { items: [], vehicleId: null, processing: false, history: null }

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

  async function analyzeItem(item, vehicleId) {
    item.status = "uploading"
    item.error = null
    renderFiles()
    try {
      if (!item.document) item.document = await API.upload(vehicleId, item.file)
      if (!item.analysis) item.analysis = await API.post(`/documents/${item.document.id}/analyze`, {})
      item.events = item.analysis.service_events || []
      if (!item.events.length) {
        const fallback = await API.post("/service-events", {
          document_id: item.document.id,
          service_type: "Mantenimiento preventivo",
          description: "Evento pendiente de confirmación manual",
          resets_maintenance_interval: true,
        })
        item.events = [fallback]
      }
      item.status = "analyzed"
    } catch (error) {
      item.status = "error"
      item.error = error.message
    }
    renderFiles()
  }

  function eventIds() {
    return [...new Set(state.items.filter((item) => item.status === "analyzed").flatMap((item) => item.events.map((event) => event.id)))]
  }

  async function refreshHistory({ announce = true } = {}) {
    const ids = eventIds()
    if (!ids.length) throw new Error("No fue posible extraer eventos de los documentos seleccionados.")
    state.history = await API.post("/history-validations", {
      vehicle_id: Number(vehicleSelect.value),
      event_ids: ids,
    })
    renderHistory(state.history)
    if (announce) UI.notice(validationNotice, "Historial completo analizado y ordenado cronológicamente.", "success")
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
    progressBar.max = state.items.filter((item) => item.status !== "analyzed").length || 1
    progressBar.value = 0
    UI.notice(validationNotice, "Analizando todos los documentos antes de construir la línea temporal…", "info")
    try {
      const pending = state.items.filter((item) => item.status !== "analyzed")
      for (let index = 0; index < pending.length; index += 1) {
        progressLabel.textContent = `Analizando ${pending[index].file.name}`
        progressCount.textContent = `${index + 1} de ${pending.length}`
        await analyzeItem(pending[index], Number(vehicleSelect.value))
        progressBar.value = index + 1
      }
      progressLabel.textContent = "Construyendo línea temporal consolidada"
      progressCount.textContent = ""
      await refreshHistory()
      const failed = state.items.filter((item) => item.status === "error").length
      if (failed) UI.notice(validationNotice, `El historial se calculó, pero ${failed} documento${failed === 1 ? "" : "s"} no pudieron analizarse. Puedes reintentarlos.`, "error")
    } catch (error) {
      UI.notice(validationNotice, error.message, "error")
    } finally {
      state.processing = false
      progress.classList.add("hidden")
      syncAnalyzeButton()
      renderFiles()
    }
  })

  historyResult.addEventListener("submit", async (event) => {
    const form = event.target.closest("form.history-correction")
    if (!form) return
    event.preventDefault()
    const submit = form.querySelector("button[type='submit']")
    const data = new FormData(form)
    submit.disabled = true
    try {
      await API.put(`/service-events/${form.dataset.eventId}`, {
        service_date: data.get("service_date"),
        mileage_km: Number(data.get("mileage_km")),
      })
      await refreshHistory({ announce: false })
      UI.notice(validationNotice, "Datos confirmados. Se recalculó la línea temporal completa.", "success")
    } catch (error) {
      UI.notice(validationNotice, error.message, "error")
      submit.disabled = false
    }
  })

  UI.vehicleOptions(vehicleSelect).then(syncAnalyzeButton).catch((error) => UI.notice(validationNotice, error.message, "error"))
  renderFiles()
}
