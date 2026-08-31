const historyNotice = document.querySelector("#history-notice")
const historyTable = document.querySelector("#history-table")
const historyVehicle = document.querySelector("#history-vehicle")

async function loadHistory() {
  const params = new URLSearchParams()
  const vehicleId = historyVehicle.value; const status = document.querySelector("#history-status").value; const from = document.querySelector("#history-from").value; const to = document.querySelector("#history-to").value
  if (vehicleId) params.set("vehicle_id", vehicleId); if (status) params.set("status", status); if (from) params.set("date_from", from); if (to) params.set("date_to", to)
  historyTable.innerHTML = '<tr><td colspan="7" class="loading">Cargando historial…</td></tr>'
  try {
    const items = await API.get(`/validations?${params}`)
    historyTable.innerHTML = items.length ? items.map((item) => `<tr><td><strong>${UI.escape(item.validation_code)}</strong></td><td>${UI.escape(item.vehicle_internal_number || `Vehículo #${item.vehicle_id}`)}</td><td>${UI.date(item.document_date)}</td><td>${UI.km(item.document_odometer)}</td><td>${UI.status(item.status)}</td><td>${UI.dateTime(item.created_at)}</td><td><a class="button button-secondary button-small" href="/results.html?id=${item.id}">Ver detalle</a></td></tr>`).join("") : '<tr><td colspan="7" class="empty">No hay validaciones que coincidan con los filtros.</td></tr>'
  } catch (error) { UI.notice(historyNotice, error.message, "error"); historyTable.innerHTML = '<tr><td colspan="7" class="empty">No se pudo cargar el historial.</td></tr>' }
}
document.querySelector("#history-filter").addEventListener("click", loadHistory)
UI.vehicleOptions(historyVehicle, true)
  .then(() => { historyVehicle.options[0].textContent = "Todos los vehículos"; return loadHistory() })
  .catch((error) => UI.notice(historyNotice, error.message, "error"))
