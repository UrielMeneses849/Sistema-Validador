function resultSymbol(status) {
  return {
    APROBADO: "✓", RECHAZADO: "×", FUERA_DE_VENTANA: "○", REVISION_REQUERIDA: "!",
    COMPLIANT: "✓", EXCEEDED_MILEAGE: "×", EXCEEDED_TIME: "×", EXCEEDED_BOTH: "×",
    FIRST_MAINTENANCE_AVAILABLE: "✓", REQUIRES_REVIEW: "!", INSUFFICIENT_DATA: "!", NOT_MAINTENANCE_EVENT: "○",
  }[status] || "?"
}

function statusLabel(status) {
  return {
    FIRST_MAINTENANCE_AVAILABLE: "Primer mantenimiento disponible",
    COMPLIANT: "Cumple intervalo",
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
  const isInterval = ["COMPLIANT", "EXCEEDED_MILEAGE", "EXCEEDED_TIME", "EXCEEDED_BOTH"].includes(item.status)
  const kilometerDetail = item.meets_kilometer_condition === null ? "No aplica para este resultado." : item.meets_kilometer_condition ? "El intervalo se mantuvo dentro del límite." : "El límite de 10,000 km fue excedido."
  const timeDetail = item.meets_time_condition === null ? "No aplica para este resultado." : item.meets_time_condition ? "El intervalo se mantuvo dentro de 6 meses." : "El límite de 6 meses fue excedido."
  const rules = isInterval ? `<div class="rule-grid">${ruleMarkup("Regla de 10,000 km", item.meets_kilometer_condition, kilometerDetail)}${ruleMarkup("Regla de 6 meses", item.meets_time_condition, timeDetail)}</div>` : ""
  const delta = item.analysis_details?.delta_km
  return `<section class="card result result-${UI.escape(item.status)}"><div class="result-heading"><span class="result-symbol">${resultSymbol(item.status)}</span><div><p class="eyebrow">Validación</p><h2>${UI.escape(statusLabel(item.status))}</h2><p>${UI.escape(item.message)}</p></div></div><div class="details-grid"><div class="detail"><span>Fecha del servicio</span><strong>${UI.date(item.document_date)}</strong></div><div class="detail"><span>Kilometraje registrado</span><strong>${UI.km(item.document_odometer)}</strong></div><div class="detail"><span>Mantenimiento anterior</span><strong>${UI.date(item.last_maintenance_date)} · ${UI.km(item.last_maintenance_odometer)}</strong></div><div class="detail"><span>Límite por kilometraje</span><strong>${UI.km(item.kilometer_limit)}</strong></div><div class="detail"><span>Límite por fecha</span><strong>${UI.date(item.date_limit)}</strong></div><div class="detail"><span>Delta de kilometraje</span><strong>${UI.km(delta)}</strong></div></div>${rules}${item.reasons?.length ? `<ul class="reason-list">${item.reasons.map((reason) => `<li>${UI.escape(reason)}</li>`).join("")}</ul>` : ""}${includeAudit ? `<h3 class="card-title" style="margin-top:22px">Trazabilidad</h3><ol class="timeline">${item.audit_logs?.map((event) => `<li><span class="event-time">${UI.dateTime(event.created_at)} · ${UI.escape(event.event_type)}</span>${UI.escape(event.message)}</li>`).join("") || "<li>Sin eventos registrados.</li>"}</ol>` : ""}</section>`
}
window.resultMarkup = resultMarkup

function evidenceValue(fields, name) {
  return fields?.[name]?.normalized_value ?? "—"
}

function analysisMarkup(analysis, event) {
  const fields = analysis.extracted_fields || {}
  const confidence = analysis.confidence === "high" ? "Alta" : analysis.confidence === "medium" ? "Media" : "Baja"
  return `<div class="analysis-heading"><div><p class="eyebrow">Documento analizado</p><h2>${UI.escape(analysis.document_type.replaceAll("_", " "))}</h2></div><span class="status status-${UI.escape(analysis.confidence)}">Confianza ${confidence}</span></div><div class="analysis-grid"><div><span>Vehículo</span><strong>${UI.escape(`${evidenceValue(fields, "brand")} ${evidenceValue(fields, "model")} ${evidenceValue(fields, "year")}`)}</strong></div><div><span>VIN</span><strong>${UI.escape(evidenceValue(fields, "vin"))}</strong></div><div><span>Placas</span><strong>${UI.escape(evidenceValue(fields, "plates"))}</strong></div><div><span>Fuente</span><strong>${analysis.extraction_method === "pdf_text" ? "PDF digital" : UI.escape(analysis.extraction_method)}</strong></div><div><span>Fecha detectada</span><strong>${UI.date(event?.service_date)}</strong></div><div><span>Kilometraje detectado</span><strong>${UI.km(event?.mileage_km)}</strong></div><div class="analysis-full"><span>Servicio detectado</span><strong>${UI.escape(event?.description || "No se detectó un evento de servicio")}</strong></div></div>${analysis.warnings?.length ? `<p class="hint">${analysis.warnings.map(UI.escape).join(" · ")}</p>` : ""}`
}

const validationForm = document.querySelector("#validation-form")
const validationNotice = document.querySelector("#validation-notice")
const validateButton = document.querySelector("#validate-button")
const analysisSummary = document.querySelector("#analysis-summary")
let automaticEventId = null
let uploadedDocumentId = null

function fillCorrectionFields(event) {
  document.querySelector("#document-date").value = event.service_date || ""
  document.querySelector("#document-odometer").value = event.mileage_km ?? ""
  document.querySelector("#document-type").value = event.service_type || ""
  document.querySelector("#provider-name").value = event.dealer || ""
  document.querySelector("#invoice-number").value = event.repair_order_number || ""
  document.querySelector("#service-description").value = event.description || ""
}

function correctionPayload() {
  const mileage = document.querySelector("#document-odometer").value
  return {
    service_date: document.querySelector("#document-date").value || null,
    mileage_km: mileage === "" ? null : Number(mileage),
    service_type: document.querySelector("#document-type").value || null,
    dealer: document.querySelector("#provider-name").value || null,
    repair_order_number: document.querySelector("#invoice-number").value || null,
    description: document.querySelector("#service-description").value || null,
  }
}

function showValidation(result) {
  const target = document.querySelector("#instant-result")
  target.classList.remove("hidden")
  target.innerHTML = `${resultMarkup(result)}<div class="form-actions"><a class="button button-secondary" href="/results.html?id=${result.id}">Ver detalle y trazabilidad</a><a class="button button-primary" href="/history.html">Consultar historial</a></div>`
  target.scrollIntoView({ behavior: "smooth", block: "start" })
}

async function analyzeNewDocument(formData) {
  const vehicleId = Number(formData.get("vehicle_id"))
  const file = formData.get("document")
  if (!file?.name) throw new Error("Selecciona un documento para analizar.")
  UI.notice(validationNotice, "Analizando documento…", "info")
  const uploaded = await API.upload(vehicleId, file)
  uploadedDocumentId = uploaded.id
  const analysis = await API.post(`/documents/${uploaded.id}/analyze`, {})
  const event = analysis.service_events?.[0]
  analysisSummary.classList.remove("hidden")
  analysisSummary.innerHTML = analysisMarkup(analysis, event)
  if (!event) {
    UI.notice(validationNotice, "No se detectó un evento. Completa los campos de corrección para usar la captura manual como fallback.", "info")
    return null
  }
  automaticEventId = event.id
  fillCorrectionFields(event)
  return API.post(`/service-events/${event.id}/validate`, {})
}

if (validationForm) {
  document.querySelector("#document-file").addEventListener("change", (event) => { document.querySelector("#file-name").textContent = event.target.files[0]?.name || "Selecciona un PDF, JPG, JPEG o PNG" })
  validationForm.addEventListener("submit", async (event) => {
    event.preventDefault()
    UI.clearNotice(validationNotice)
    const formData = new FormData(validationForm)
    validateButton.disabled = true
    try {
      let result
      if (automaticEventId) {
        validateButton.textContent = "Guardando correcciones…"
        await API.put(`/service-events/${automaticEventId}`, correctionPayload())
        result = await API.post(`/service-events/${automaticEventId}/validate`, {})
      } else if (uploadedDocumentId) {
        const date = document.querySelector("#document-date").value
        const mileage = document.querySelector("#document-odometer").value
        if (!date || mileage === "") throw new Error("Completa fecha y kilometraje para usar la captura manual.")
        const manualEvent = await API.post("/service-events", { document_id: uploadedDocumentId, ...correctionPayload() })
        automaticEventId = manualEvent.id
        result = await API.post(`/service-events/${manualEvent.id}/validate`, {})
      } else {
        validateButton.textContent = "Analizando documento…"
        result = await analyzeNewDocument(formData)
      }
      if (result) {
        showValidation(result)
        UI.notice(validationNotice, "Documento analizado y validación registrada.", "success")
        validateButton.textContent = "✓ GUARDAR CORRECCIONES Y REVALIDAR"
      }
    } catch (error) {
      UI.notice(validationNotice, error.message, "error")
    } finally {
      validateButton.disabled = false
      if (!automaticEventId) validateButton.textContent = "✦ ANALIZAR Y VALIDAR DOCUMENTO"
    }
  })
  UI.vehicleOptions(document.querySelector("#validation-vehicle")).catch((error) => UI.notice(validationNotice, error.message, "error"))
}
