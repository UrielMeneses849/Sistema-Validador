const vehicleForm = document.querySelector("#vehicle-form")
const vehicleNotice = document.querySelector("#vehicle-notice")
let listedVehicles = []
let editingVehicleId = null

function renderVehicles() {
  const table = document.querySelector("#vehicles-table")
  table.innerHTML = listedVehicles.length ? listedVehicles.map((vehicle) => `<tr>
    <td><strong>${UI.escape(vehicle.internal_number)}</strong><br><span class="muted">${UI.escape(vehicle.vin || "Sin VIN")}</span></td>
    <td>${UI.escape(vehicle.plate)}</td><td>${UI.escape(vehicle.brand)} ${UI.escape(vehicle.model)}</td><td>${vehicle.year}</td><td>${UI.km(vehicle.current_odometer)}</td><td>${UI.status(vehicle.status)}</td>
    <td><div class="actions"><button class="button button-secondary button-small" data-action="edit" data-id="${vehicle.id}" type="button">Editar</button>${vehicle.status === "active" ? `<button class="button button-danger button-small" data-action="deactivate" data-id="${vehicle.id}" type="button">Desactivar</button>` : ""}</div></td></tr>`).join("") : '<tr><td colspan="7" class="empty">No hay vehículos que coincidan con la búsqueda.</td></tr>'
}

async function loadVehicles() {
  const search = document.querySelector("#vehicle-search").value.trim()
  const status = document.querySelector("#vehicle-status-filter").value
  const query = new URLSearchParams()
  if (search) query.set("search", search)
  if (status) query.set("status", status)
  try { listedVehicles = await API.get(`/vehicles?${query}`); renderVehicles() } catch (error) { UI.notice(vehicleNotice, error.message, "error") }
}

function resetForm() {
  editingVehicleId = null
  vehicleForm.reset()
  vehicleForm.elements.status.value = "active"
  document.querySelector("#vehicle-form-title").textContent = "Registrar vehículo"
  document.querySelector("#vehicle-submit").textContent = "Guardar vehículo"
  document.querySelector("#cancel-edit").classList.add("hidden")
  UI.clearNotice(vehicleNotice)
}

vehicleForm.addEventListener("submit", async (event) => {
  event.preventDefault(); UI.clearNotice(vehicleNotice)
  const data = Object.fromEntries(new FormData(vehicleForm))
  data.year = Number(data.year); data.current_odometer = Number(data.current_odometer); data.vin = data.vin || null
  const isEditing = Boolean(editingVehicleId)
  try {
    await (editingVehicleId ? API.put(`/vehicles/${editingVehicleId}`, data) : API.post("/vehicles", data))
    resetForm()
    UI.notice(vehicleNotice, isEditing ? "Vehículo actualizado correctamente." : "Vehículo registrado correctamente.", "success")
    await loadVehicles()
  } catch (error) { UI.notice(vehicleNotice, error.message, "error") }
})

document.querySelector("#vehicles-table").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]"); if (!button) return
  const vehicle = listedVehicles.find((item) => item.id === Number(button.dataset.id)); if (!vehicle) return
  if (button.dataset.action === "edit") {
    editingVehicleId = vehicle.id
    for (const [key, value] of Object.entries(vehicle)) if (vehicleForm.elements[key]) vehicleForm.elements[key].value = value ?? ""
    document.querySelector("#vehicle-form-title").textContent = `Editar ${vehicle.internal_number}`
    document.querySelector("#vehicle-submit").textContent = "Guardar cambios"
    document.querySelector("#cancel-edit").classList.remove("hidden")
    window.scrollTo({ top: 0, behavior: "smooth" })
  }
  if (button.dataset.action === "deactivate" && window.confirm(`¿Desactivar ${vehicle.internal_number}? Su historial se conservará.`)) {
    try { await API.remove(`/vehicles/${vehicle.id}`); await loadVehicles() } catch (error) { UI.notice(vehicleNotice, error.message, "error") }
  }
})
document.querySelector("#search-vehicles").addEventListener("click", loadVehicles)
document.querySelector("#vehicle-search").addEventListener("keydown", (event) => { if (event.key === "Enter") loadVehicles() })
document.querySelector("#cancel-edit").addEventListener("click", resetForm)
loadVehicles()
