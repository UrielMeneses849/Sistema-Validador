const API = {
  async request(path, options = {}) {
    const response = await fetch(`/api${path}`, options)
    const body = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = Array.isArray(body?.detail)
        ? body.detail.map((item) => item.msg).join(" ")
        : body?.detail || "No se pudo completar la solicitud."
      throw new Error(detail)
    }
    return body
  },
  get(path) { return this.request(path) },
  post(path, payload) { return this.request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }) },
  put(path, payload) { return this.request(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }) },
  remove(path) { return this.request(path, { method: "DELETE" }) },
  upload(vehicleId, file) {
    const form = new FormData()
    form.append("vehicle_id", vehicleId)
    form.append("file", file)
    return this.request("/documents/upload", { method: "POST", body: form })
  },
}

const UI = {
  escape(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#039;", '"': "&quot;" })[char])
  },
  date(value) {
    if (!value) return "—"
    return new Intl.DateTimeFormat("es-MX", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T12:00:00`))
  },
  dateTime(value) {
    if (!value) return "—"
    return new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short" }).format(new Date(`${value}Z`))
  },
  km(value) { return value === null || value === undefined ? "—" : `${Number(value).toLocaleString("es-MX")} km` },
  status(status) { return `<span class="status status-${this.escape(status)}">${this.escape(status?.replaceAll("_", " "))}</span>` },
  notice(element, message, type = "info") {
    element.textContent = message
    element.className = `notice ${type} show`
  },
  clearNotice(element) { element.textContent = ""; element.className = "notice" },
  async vehicleOptions(select, includeInactive = false) {
    const vehicles = await API.get(`/vehicles${includeInactive ? "" : "?status=active"}`)
    select.innerHTML = `<option value="">Selecciona un vehículo</option>${vehicles.map((v) => `<option value="${v.id}">${this.escape(v.internal_number)} · ${this.escape(v.plate)} · ${this.escape(v.brand)} ${this.escape(v.model)}</option>`).join("")}`
    return vehicles
  },
}

window.API = API
window.UI = UI

