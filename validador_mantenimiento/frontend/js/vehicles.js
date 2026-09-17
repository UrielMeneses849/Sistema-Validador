const LEGACY_STORAGE_KEY = "intelidrive.vehicles.local.v1"
const LOCAL_IMPORT_MARKER = "intelidrive.vehicles.local.v1.imported-to-api"
const INITIAL_CONTRACT_NUMBER = 835414
const MAX_CONTRACT_NUMBER = 999999

const vehicleForm = document.querySelector("#vehicle-form")
const vehicleNotice = document.querySelector("#vehicle-notice")
const vehicleSearch = document.querySelector("#vehicle-search")
const vehiclesTable = document.querySelector("#vehicles-table")
const deleteDialog = document.querySelector("#vehicle-delete-dialog")
const deleteForm = document.querySelector("#vehicle-delete-form")
const deleteCancel = document.querySelector("#cancel-vehicle-delete")
const deleteConfirm = document.querySelector("#confirm-vehicle-delete")
const deleteError = document.querySelector("#vehicle-delete-error")
let vehicles = []
let listedVehicles = []
let manufacturerRules = []
let editingVehicleId = null
let pendingDeleteVehicleId = null
let contractRequest = 0

const requiredFields = {
  vehicle_condition: "Condición del vehículo",
  model: "Modelo",
  kilometraje: "Kilometraje",
  initial_odometer: "Kilometraje al inicio del contrato",
  fecha_factura_origen: "Fecha factura de origen",
  fecha_inicio_contrato: "Fecha inicio de contrato",
  fecha_fin_contrato: "Fecha fin de contrato",
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#039;", "\"": "&quot;",
  })[character])
}

function normalizeText(value) {
  return String(value ?? "").trim()
}

function sixDigitValue(value) {
  const normalizedValue = normalizeText(value)
  return /^\d{6}$/.test(normalizedValue) ? normalizedValue : null
}

function contractNumberOf(record) {
  return sixDigitValue(record?.numero_contrato)
}

function displayContract(record) {
  return contractNumberOf(record)
    || normalizeText(record?.internal_number)
    || sixDigitValue(record?.id)
    || null
}

function nextContract(records) {
  const contractNumbers = records
    .map(contractNumberOf)
    .filter(Boolean)
    .map(Number)
  const nextNumber = contractNumbers.length
    ? Math.max(...contractNumbers) + 1
    : INITIAL_CONTRACT_NUMBER

  return nextNumber <= MAX_CONTRACT_NUMBER ? String(nextNumber).padStart(6, "0") : null
}

function readLegacyVehicles() {
  try {
    const savedValue = window.localStorage.getItem(LEGACY_STORAGE_KEY)
    if (!savedValue) return { records: [], error: null }

    const records = JSON.parse(savedValue)
    if (!Array.isArray(records)) {
      return { records: [], error: "No se pudo leer el respaldo local de vehículos." }
    }

    return { records, error: null }
  } catch {
    return { records: [], error: "No se pudo leer el respaldo local de vehículos." }
  }
}

function hasImportedLegacyVehicles() {
  try {
    return window.localStorage.getItem(LOCAL_IMPORT_MARKER) === "1"
  } catch {
    return false
  }
}

function markLegacyVehiclesImported() {
  try {
    window.localStorage.setItem(LOCAL_IMPORT_MARKER, "1")
  } catch {
    // La importación ya quedó en la API; si no se puede guardar el marcador,
    // el backend evita duplicados por número de contrato en un siguiente intento.
  }
}

function showNotice(message, type = "info") {
  vehicleNotice.textContent = message
  vehicleNotice.className = `notice ${type} show`
}

function clearNotice() {
  vehicleNotice.textContent = ""
  vehicleNotice.className = "notice"
}

function vehicleMileage(record) {
  return record?.kilometraje ?? record?.current_odometer
}

function conditionLabel(value) {
  return value === "new" ? "Nuevo (M1)" : value === "used" ? "Seminuevo (M2)" : "Sin definir"
}

function formatKilometers(value) {
  const number = Number(value)
  return Number.isFinite(number) ? `${number.toLocaleString("es-MX")} km` : "—"
}

function filterVehicles(records, search) {
  const searchTerm = normalizeText(search).toLocaleLowerCase("es-MX")
  if (!searchTerm) return records

  return records.filter((record) => [
    displayContract(record),
    record?.brand,
    record?.model,
  ].some((value) => normalizeText(value).toLocaleLowerCase("es-MX").includes(searchTerm)))
}

function renderVehicles() {
  vehiclesTable.innerHTML = listedVehicles.length
    ? listedVehicles.map((vehicle) => {
      const contract = displayContract(vehicle)
      const canEdit = Boolean(contractNumberOf(vehicle))
      const canDelete = Number.isSafeInteger(Number(vehicle?.id)) && Number(vehicle.id) > 0
      const actions = [
        canEdit ? `<button class="button button-secondary button-small" data-action="edit" data-id="${escapeHtml(vehicle.id)}" type="button">Editar</button>` : "",
        canDelete ? `<button class="button button-danger button-small" data-action="delete" data-id="${escapeHtml(vehicle.id)}" type="button">Eliminar</button>` : "",
      ].filter(Boolean).join("")
      return `<tr>
        <td><strong>${escapeHtml(contract || "—")}</strong></td>
        <td>${escapeHtml(conditionLabel(vehicle?.vehicle_condition))}</td>
        <td>${escapeHtml(`${normalizeText(vehicle?.brand) || "—"} ${normalizeText(vehicle?.model) || ""}`.trim())}</td>
        <td>${escapeHtml(formatKilometers(vehicleMileage(vehicle)))}</td>
        <td>${actions ? `<div class="actions">${actions}</div>` : "—"}</td>
      </tr>`
    }).join("")
    : '<tr><td colspan="5" class="empty">No hay vehículos que coincidan con la búsqueda.</td></tr>'
}

function applySearch() {
  listedVehicles = filterVehicles(vehicles, vehicleSearch.value)
  renderVehicles()
}

async function loadVehicles({ announceError = true } = {}) {
  try {
    vehicles = await API.get("/vehicles")
    applySearch()
    return vehicles
  } catch (error) {
    if (announceError) showNotice(error.message, "error")
    return null
  }
}

async function loadManufacturerRules() {
  const select = vehicleForm.elements.brand_policy
  try {
    manufacturerRules = await API.get("/manufacturer-rules?active=true")
    select.innerHTML = `<option value="">Selecciona una marca</option>${manufacturerRules.map((rule) => `<option value="${escapeHtml(rule.brand)}">${escapeHtml(rule.brand)} · ${rule.interval_months} meses / ${Number(rule.interval_km).toLocaleString("es-MX")} km</option>`).join("")}`
  } catch (error) {
    manufacturerRules = []
    select.innerHTML = '<option value="">No fue posible cargar las políticas</option>'
    showNotice(error.message, "error")
  }
}

function updateBrandField() {
  const isNew = vehicleForm.elements.vehicle_condition.value === "new"
  const textField = document.querySelector("#brand-text-field")
  const policyField = document.querySelector("#brand-policy-field")
  const brandInput = vehicleForm.elements.brand
  const policySelect = vehicleForm.elements.brand_policy
  textField.classList.toggle("hidden", isNew)
  policyField.classList.toggle("hidden", !isNew)
  brandInput.disabled = isNew
  brandInput.required = !isNew
  policySelect.disabled = !isNew
  policySelect.required = isNew
}

function clearFieldErrors() {
  Array.from(vehicleForm.elements).forEach((field) => {
    if (typeof field.setCustomValidity === "function") field.setCustomValidity("")
  })
}

async function setNewContract() {
  const requestId = ++contractRequest
  const contractField = vehicleForm.elements.numero_contrato
  contractField.value = ""
  contractField.setCustomValidity("")

  try {
    const result = await API.get("/vehicles/next-contract")
    const contract = sixDigitValue(result?.numero_contrato)
    if (!contract) throw new Error("La API no devolvió un número de contrato válido.")
    if (requestId !== contractRequest || editingVehicleId) return
    contractField.value = contract
  } catch (error) {
    // La vista previa local sólo se usa si la API aún no está disponible. Al guardar,
    // el servidor sigue siendo quien asigna y garantiza el consecutivo definitivo.
    const fallback = nextContract(vehicles)
    if (requestId !== contractRequest || editingVehicleId) return
    contractField.value = fallback || ""
    contractField.setCustomValidity(fallback ? "" : "No hay números de contrato de seis dígitos disponibles.")
    if (!fallback) showNotice(error.message, "error")
  }
}

async function resetForm({ clearMessage = true } = {}) {
  editingVehicleId = null
  vehicleForm.reset()
  vehicleForm.elements.initial_odometer.readOnly = false
  clearFieldErrors()
  document.querySelector("#vehicle-form-title").textContent = "Registrar vehículo"
  document.querySelector("#vehicle-submit").textContent = "Guardar vehículo"
  document.querySelector("#cancel-edit").classList.add("hidden")
  updateBrandField()
  if (clearMessage) clearNotice()
  await setNewContract()
}

function isValidDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [year, month, day] = value.split("-").map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
}

function formValues() {
  const data = Object.fromEntries(new FormData(vehicleForm))
  return {
    vehicle_condition: normalizeText(data.vehicle_condition),
    brand: normalizeText(data.vehicle_condition === "new" ? data.brand_policy : data.brand),
    model: normalizeText(data.model),
    kilometraje: normalizeText(data.kilometraje),
    initial_odometer: normalizeText(data.initial_odometer),
    fecha_factura_origen: normalizeText(data.fecha_factura_origen),
    numero_contrato: normalizeText(data.numero_contrato),
    fecha_inicio_contrato: normalizeText(data.fecha_inicio_contrato),
    fecha_fin_contrato: normalizeText(data.fecha_fin_contrato),
  }
}

function validateForm(values) {
  clearFieldErrors()
  const errors = []
  const addError = (fieldName, message) => {
    const field = vehicleForm.elements[fieldName]
    field.setCustomValidity(message)
    errors.push({ field, message })
  }

  Object.entries(requiredFields).forEach(([fieldName, label]) => {
    if (!values[fieldName]) addError(fieldName, `${label} es obligatorio.`)
  })

  if (!values.brand) {
    addError(values.vehicle_condition === "new" ? "brand_policy" : "brand", "Marca es obligatoria.")
  }

  if (!/^\d{6}$/.test(values.numero_contrato)) {
    addError("numero_contrato", "El número de contrato debe tener seis dígitos.")
  }

  const mileage = Number(values.kilometraje)
  if (values.kilometraje && (!Number.isInteger(mileage) || mileage < 0)) {
    addError("kilometraje", "El kilometraje debe ser un número entero igual o mayor a cero.")
  }
  const initialOdometer = Number(values.initial_odometer)
  if (values.initial_odometer && (!Number.isInteger(initialOdometer) || initialOdometer < 0)) {
    addError("initial_odometer", "El kilometraje inicial debe ser un número entero igual o mayor a cero.")
  }

  ["fecha_factura_origen", "fecha_inicio_contrato", "fecha_fin_contrato"].forEach((fieldName) => {
    if (values[fieldName] && !isValidDate(values[fieldName])) {
      addError(fieldName, "Ingresa una fecha válida.")
    }
  })

  if (
    isValidDate(values.fecha_inicio_contrato)
    && isValidDate(values.fecha_fin_contrato)
    && values.fecha_fin_contrato < values.fecha_inicio_contrato
  ) {
    addError("fecha_fin_contrato", "La fecha de fin de contrato no puede ser anterior a la fecha de inicio.")
  }

  if (errors.length) {
    showNotice(errors[0].message, "error")
    errors[0].field.focus()
    vehicleForm.reportValidity()
    return null
  }

  return { ...values, kilometraje: mileage, initial_odometer: initialOdometer }
}

function vehiclePayload(values, { includeContract = false } = {}) {
  const payload = {
    brand: values.brand,
    model: values.model,
    kilometraje: values.kilometraje,
    vehicle_condition: values.vehicle_condition,
    initial_odometer: values.initial_odometer,
    fecha_factura_origen: values.fecha_factura_origen,
    fecha_inicio_contrato: values.fecha_inicio_contrato,
    fecha_fin_contrato: values.fecha_fin_contrato,
  }
  if (includeContract) payload.numero_contrato = values.numero_contrato
  return payload
}

function isImportableLegacyVehicle(record) {
  const values = {
    brand: normalizeText(record?.brand),
    model: normalizeText(record?.model),
    kilometraje: normalizeText(vehicleMileage(record)),
    fecha_factura_origen: normalizeText(record?.fecha_factura_origen),
    numero_contrato: contractNumberOf(record) || sixDigitValue(record?.id) || "",
    fecha_inicio_contrato: normalizeText(record?.fecha_inicio_contrato),
    fecha_fin_contrato: normalizeText(record?.fecha_fin_contrato),
  }
  const mileage = Number(values.kilometraje)
  return Boolean(
    values.brand
    && values.model
    && values.numero_contrato
    && Number.isInteger(mileage)
    && mileage >= 0
    && isValidDate(values.fecha_factura_origen)
    && isValidDate(values.fecha_inicio_contrato)
    && isValidDate(values.fecha_fin_contrato)
    && values.fecha_fin_contrato >= values.fecha_inicio_contrato
  )
}

function localVehiclePayload(record) {
  return vehiclePayload({
    brand: normalizeText(record?.brand),
    model: normalizeText(record?.model),
    kilometraje: Number(vehicleMileage(record)),
    fecha_factura_origen: normalizeText(record?.fecha_factura_origen),
    numero_contrato: contractNumberOf(record) || sixDigitValue(record?.id) || "",
    fecha_inicio_contrato: normalizeText(record?.fecha_inicio_contrato),
    fecha_fin_contrato: normalizeText(record?.fecha_fin_contrato),
  }, { includeContract: true })
}

async function importLegacyVehiclesOnce() {
  if (hasImportedLegacyVehicles()) return { imported: 0, error: null }

  const local = readLegacyVehicles()
  if (local.error) return { imported: 0, error: local.error }

  const knownContracts = new Set(vehicles.map(contractNumberOf).filter(Boolean))
  const records = local.records.filter(isImportableLegacyVehicle)
  let imported = 0

  try {
    for (const record of records) {
      const contract = contractNumberOf(record) || sixDigitValue(record?.id)
      if (!contract || knownContracts.has(contract)) continue
      const saved = await API.post("/vehicles", localVehiclePayload(record))
      knownContracts.add(contractNumberOf(saved) || contract)
      imported += 1
    }
    markLegacyVehiclesImported()
    return { imported, error: null }
  } catch (error) {
    return { imported, error: error.message || "No se pudo importar el respaldo local de vehículos." }
  }
}

function startEditing(vehicleId) {
  const vehicle = vehicles.find((item) => item.id === vehicleId)
  const contract = contractNumberOf(vehicle)
  if (!vehicle || !contract) {
    showNotice("Este vehículo no tiene un contrato compatible para editar desde este formulario.", "error")
    return
  }

  editingVehicleId = vehicleId
  contractRequest += 1
  clearFieldErrors()
  vehicleForm.elements.vehicle_condition.value = ["new", "used"].includes(vehicle?.vehicle_condition)
    ? vehicle.vehicle_condition
    : ""
  updateBrandField()
  vehicleForm.elements.brand.value = vehicle?.brand ?? ""
  vehicleForm.elements.brand_policy.value = vehicle?.vehicle_condition === "new" ? vehicle?.brand ?? "" : ""
  vehicleForm.elements.model.value = vehicle?.model ?? ""
  vehicleForm.elements.kilometraje.value = vehicleMileage(vehicle) ?? ""
  vehicleForm.elements.initial_odometer.value = vehicle?.initial_odometer ?? ""
  vehicleForm.elements.initial_odometer.readOnly = vehicle?.initial_odometer !== null && vehicle?.initial_odometer !== undefined
  vehicleForm.elements.fecha_factura_origen.value = vehicle?.fecha_factura_origen ?? ""
  vehicleForm.elements.numero_contrato.value = contract
  vehicleForm.elements.fecha_inicio_contrato.value = vehicle?.fecha_inicio_contrato ?? ""
  vehicleForm.elements.fecha_fin_contrato.value = vehicle?.fecha_fin_contrato ?? ""
  document.querySelector("#vehicle-form-title").textContent = `Editar contrato ${contract}`
  document.querySelector("#vehicle-submit").textContent = "Guardar cambios"
  document.querySelector("#cancel-edit").classList.remove("hidden")
  clearNotice()
  window.scrollTo({ top: 0, behavior: "smooth" })
}

function closeDeleteDialog() {
  pendingDeleteVehicleId = null
  deleteError.textContent = ""
  deleteError.className = "notice"
  if (deleteDialog.open) deleteDialog.close()
}

function requestVehicleDeletion(vehicleId) {
  const vehicle = vehicles.find((item) => item.id === vehicleId)
  if (!vehicle) {
    showNotice("No fue posible identificar el vehículo que se desea eliminar.", "error")
    return
  }

  pendingDeleteVehicleId = vehicleId
  document.querySelector("#delete-vehicle-contract").textContent = displayContract(vehicle) || "Sin número de contrato"
  document.querySelector("#delete-vehicle-brand").textContent = normalizeText(vehicle.brand) || "—"
  document.querySelector("#delete-vehicle-model").textContent = normalizeText(vehicle.model) || "—"
  deleteError.textContent = ""
  deleteError.className = "notice"
  deleteDialog.showModal()
}

vehicleForm.addEventListener("submit", async (event) => {
  event.preventDefault()
  clearNotice()

  const values = validateForm(formValues())
  if (!values) return

  const isEditing = editingVehicleId !== null
  const submitButton = document.querySelector("#vehicle-submit")
  submitButton.disabled = true
  try {
    const saved = isEditing
      ? await API.put(`/vehicles/${editingVehicleId}`, vehiclePayload(values))
      : await API.post("/vehicles", vehiclePayload(values))
    await loadVehicles({ announceError: false })
    await resetForm({ clearMessage: false })
    const savedContract = contractNumberOf(saved) || values.numero_contrato
    showNotice(isEditing ? "Vehículo actualizado correctamente." : `Vehículo registrado con contrato ${savedContract}.`, "success")
  } catch (error) {
    showNotice(error.message, "error")
  } finally {
    submitButton.disabled = false
  }
})

vehiclesTable.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]")
  if (button?.dataset.action === "edit") startEditing(Number(button.dataset.id))
  if (button?.dataset.action === "delete") requestVehicleDeletion(Number(button.dataset.id))
})

deleteCancel.addEventListener("click", closeDeleteDialog)

deleteDialog.addEventListener("cancel", (event) => {
  event.preventDefault()
  closeDeleteDialog()
})

deleteForm.addEventListener("submit", async (event) => {
  event.preventDefault()
  const vehicleId = pendingDeleteVehicleId
  if (!vehicleId) return

  deleteConfirm.disabled = true
  try {
    await API.remove(`/vehicles/${vehicleId}`)
    vehicles = vehicles.filter((vehicle) => vehicle.id !== vehicleId)
    applySearch()
    if (editingVehicleId === vehicleId) await resetForm({ clearMessage: false })
    closeDeleteDialog()
    showNotice("Vehículo eliminado correctamente.", "success")
  } catch (error) {
    deleteError.textContent = error.message
    deleteError.className = "notice error show"
  } finally {
    deleteConfirm.disabled = false
  }
})

document.querySelector("#search-vehicles").addEventListener("click", async () => {
  await loadVehicles()
})

vehicleSearch.addEventListener("keydown", async (event) => {
  if (event.key === "Enter") {
    event.preventDefault()
    await loadVehicles()
  }
})

document.querySelector("#cancel-edit").addEventListener("click", async () => {
  await resetForm()
})

vehicleForm.addEventListener("input", (event) => {
  if (typeof event.target.setCustomValidity === "function") event.target.setCustomValidity("")
  if (event.target.name === "vehicle_condition") updateBrandField()
})

async function initializeVehiclesPage() {
  await loadManufacturerRules()
  const loaded = await loadVehicles()
  if (!loaded) return

  const imported = await importLegacyVehiclesOnce()
  if (imported.imported) await loadVehicles({ announceError: false })
  await resetForm({ clearMessage: false })

  if (imported.error) {
    showNotice(imported.error, "error")
  } else if (imported.imported) {
    showNotice(`Se importó ${imported.imported} vehículo${imported.imported === 1 ? "" : "s"} del respaldo local.`, "success")
  }
}

initializeVehiclesPage()
