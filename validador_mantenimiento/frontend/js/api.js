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
  extractContract(file) {
    const form = new FormData()
    form.append("file", file)
    return this.request("/vehicles/extract-contract", { method: "POST", body: form })
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

// Keep the navigation compact across pages without changing the current form.
const appShell = document.querySelector(".app-shell")
const sidebar = appShell?.querySelector(".sidebar")
if (sidebar) {
  const compactScreen = window.matchMedia("(max-width: 800px)")
  const navigation = sidebar.querySelector("nav")
  const menuButton = document.createElement("button")
  menuButton.type = "button"
  menuButton.className = "sidebar-toggle"
  menuButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg><span></span>'
  navigation.id = "sidebar-navigation"
  menuButton.setAttribute("aria-controls", navigation.id)
  sidebar.prepend(menuButton)
  sidebar.querySelectorAll(".nav-list a").forEach((link) => {
    link.title = link.textContent.trim()
  })

  const preferenceKey = () => `intelidrive-menu-${compactScreen.matches ? "mobile" : "desktop"}`
  const setMenuCollapsed = (collapsed) => {
    appShell.classList.toggle("sidebar-collapsed", collapsed)
    const label = collapsed ? "Mostrar menú" : compactScreen.matches ? "Ocultar menú" : "Contraer menú"
    menuButton.setAttribute("aria-expanded", String(!collapsed))
    menuButton.setAttribute("aria-label", label)
    menuButton.title = label
    menuButton.querySelector("span").textContent = label
  }
  const restoreMenu = () => {
    let saved = null
    try { saved = localStorage.getItem(preferenceKey()) } catch { /* Storage may be unavailable. */ }
    setMenuCollapsed(saved === null ? compactScreen.matches : saved === "collapsed")
  }
  menuButton.addEventListener("click", () => {
    const collapsed = !appShell.classList.contains("sidebar-collapsed")
    setMenuCollapsed(collapsed)
    try { localStorage.setItem(preferenceKey(), collapsed ? "collapsed" : "expanded") } catch { /* Navigation still works without storage. */ }
  })
  compactScreen.addEventListener("change", restoreMenu)
  restoreMenu()
}

// Only change layout: hidden upload controls retain their files and selected vehicle.
const workspace = document.querySelector(".audit-workspace")
const uploadPanel = document.querySelector("#validator-upload-panel")
const expandButton = document.querySelector("#validator-expand-table")
if (workspace && uploadPanel && expandButton) {
  expandButton.addEventListener("click", () => {
    const expanded = workspace.classList.toggle("is-expanded")
    uploadPanel.hidden = expanded
    expandButton.setAttribute("aria-expanded", String(expanded))
    expandButton.textContent = expanded ? "Mostrar carga de archivos" : "Ampliar tabla"
  })
}

// Capa visual compartida: no modifica datos, rutas ni contratos funcionales.
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches

function revealInterface(root = document) {
  const elements = root.querySelectorAll?.(".page-header, .metric, .card, .criteria-section, .audit-controls, .audit-result, .process-strip") || []
  elements.forEach((element, index) => {
    if (element.dataset.motionReady) return
    element.dataset.motionReady = "true"
    element.style.setProperty("--reveal-order", Math.min(index, 8))
    element.classList.add("ui-reveal")
    requestAnimationFrame(() => element.classList.add("is-visible"))
  })
}

function animateNumber(element) {
  if (reducedMotion || element.dataset.counted === "true") return
  const target = Number(element.textContent)
  if (!Number.isFinite(target)) return
  element.dataset.counted = "true"
  const start = performance.now()
  const duration = 620
  const tick = (time) => {
    const progress = Math.min((time - start) / duration, 1)
    const eased = 1 - Math.pow(1 - progress, 3)
    element.textContent = String(Math.round(target * eased))
    if (progress < 1) requestAnimationFrame(tick)
    else element.textContent = String(target)
  }
  requestAnimationFrame(tick)
}

function updateAuditFlow() {
  const steps = document.querySelectorAll(".audit-flow > div")
  if (steps.length !== 3) return
  const processStatus = document.querySelector("#audit-process-status, #history-progress")
  const processing = Boolean(processStatus && !processStatus.classList.contains("hidden"))
  const hasResult = Boolean(document.querySelector("#audit-result .audit-history, #history-result:not(.hidden)"))
  const hasVehicle = Boolean(document.querySelector("#audit-vehicle, #validation-vehicle")?.value)
  steps.forEach((step) => step.classList.remove("active", "complete"))
  steps[0].classList.add(hasResult || processing || hasVehicle ? "complete" : "active")
  if (processing || (hasVehicle && !hasResult)) steps[1].classList.add("active")
  if (hasResult) {
    steps[1].classList.add("complete")
    steps[2].classList.add("active")
  }
}

revealInterface()
document.querySelectorAll(".metric-value").forEach((element) => {
  new MutationObserver(() => animateNumber(element)).observe(element, { childList: true, characterData: true, subtree: true })
  animateNumber(element)
})

const visualObserver = new MutationObserver((mutations) => {
  mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
    if (node.nodeType === Node.ELEMENT_NODE) revealInterface(node)
  }))
  updateAuditFlow()
})
visualObserver.observe(document.body, { childList: true, subtree: true })
const auditStatus = document.querySelector("#audit-process-status, #history-progress")
if (auditStatus) new MutationObserver(updateAuditFlow).observe(auditStatus, { attributes: true, attributeFilter: ["class"] })
const pdfResult = document.querySelector("#history-result")
if (pdfResult) new MutationObserver(updateAuditFlow).observe(pdfResult, { attributes: true, attributeFilter: ["class"] })
document.querySelector("#audit-vehicle, #validation-vehicle")?.addEventListener("change", updateAuditFlow)
updateAuditFlow()
