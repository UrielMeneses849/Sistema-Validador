async function loadDashboard() {
  const table = document.querySelector("#latest-table")
  try {
    const data = await API.get("/dashboard")
    document.querySelector("#vehicles-count").textContent = data.vehicles
    document.querySelector("#validations-count").textContent = data.validations
    document.querySelector("#approved-count").textContent = data.approved
    document.querySelector("#rejected-count").textContent = data.rejected
    document.querySelector("#outside-count").textContent = data.outside_window
    table.innerHTML = data.latest_validations.length ? data.latest_validations.map((item) => `
      <tr><td><a class="table-link" href="/results.html?id=${item.id}">${UI.escape(item.validation_code)}</a></td><td>${UI.escape(item.vehicle_internal_number || `Vehículo #${item.vehicle_id}`)}</td><td>${UI.date(item.document_date)}</td><td>${UI.km(item.document_odometer)}</td><td>${UI.status(item.status)}</td><td>${UI.dateTime(item.created_at)}</td></tr>`).join("") : '<tr><td colspan="6" class="empty">Aún no hay validaciones. Registra un vehículo y su mantenimiento de referencia para comenzar.</td></tr>'
  } catch (error) { table.innerHTML = `<tr><td colspan="6" class="empty">${UI.escape(error.message)}</td></tr>` }
}
loadDashboard()
