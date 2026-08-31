const maintenanceForm = document.querySelector("#maintenance-form")
const maintenanceNotice = document.querySelector("#maintenance-notice")
const maintenanceSelect = document.querySelector("#maintenance-vehicle")

async function loadMaintenanceHistory() {
  const vehicleId = maintenanceSelect.value; const container = document.querySelector("#maintenance-history")
  if (!vehicleId) { container.className = "empty"; container.textContent = "Selecciona un vehículo para ver su historial."; return }
  container.className = "table-wrap"; container.innerHTML = '<p class="loading">Cargando historial…</p>'
  try {
    const items = await API.get(`/vehicles/${vehicleId}/maintenances`)
    container.innerHTML = items.length ? `<table class="data-table"><thead><tr><th>Fecha</th><th>Odómetro</th><th>Tipo</th><th>Descripción</th></tr></thead><tbody>${items.map((item) => `<tr><td>${UI.date(item.maintenance_date)}</td><td>${UI.km(item.odometer)}</td><td>${UI.escape(item.maintenance_type)}</td><td>${UI.escape(item.description || "—")}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">Este vehículo aún no tiene mantenimientos registrados.</div>'
  } catch (error) { container.className = "empty"; container.textContent = error.message }
}

maintenanceForm.addEventListener("submit", async (event) => {
  event.preventDefault(); UI.clearNotice(maintenanceNotice)
  const data = Object.fromEntries(new FormData(maintenanceForm)); const vehicleId = data.vehicle_id; delete data.vehicle_id; data.odometer = Number(data.odometer); data.description = data.description || null
  try { await API.post(`/vehicles/${vehicleId}/maintenances`, data); maintenanceForm.reset(); maintenanceSelect.value = vehicleId; UI.notice(maintenanceNotice, "Mantenimiento de referencia guardado correctamente.", "success"); await loadMaintenanceHistory() } catch (error) { UI.notice(maintenanceNotice, error.message, "error") }
})
maintenanceSelect.addEventListener("change", loadMaintenanceHistory)
UI.vehicleOptions(maintenanceSelect).then(loadMaintenanceHistory).catch((error) => UI.notice(maintenanceNotice, error.message, "error"))
