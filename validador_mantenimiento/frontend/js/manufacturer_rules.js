const ruleForm = document.querySelector("#rule-form")
const ruleNotice = document.querySelector("#rule-notice")
const rulesTable = document.querySelector("#rules-table")
const ruleSubmit = document.querySelector("#rule-submit")
const ruleCancel = document.querySelector("#rule-cancel")
let rules = []
let editingRuleId = null

function rulePayload() {
  const data = new FormData(ruleForm)
  return {
    brand: String(data.get("brand") || "").trim(),
    interval_months: Number(data.get("interval_months")),
    interval_km: Number(data.get("interval_km")),
    active: data.get("active") === "on",
  }
}

function renderRules() {
  rulesTable.innerHTML = rules.length
    ? rules.map((rule) => `<tr>
      <td><strong>${UI.escape(rule.brand)}</strong></td>
      <td>${Number(rule.interval_months).toLocaleString("es-MX")}</td>
      <td>${UI.km(rule.interval_km)}</td>
      <td><span class="status status-${rule.active ? "COMPLIANT" : "REQUIRES_REVIEW"}">${rule.active ? "Activa" : "Inactiva"}</span></td>
      <td><div class="actions"><button class="button button-secondary button-small" data-action="edit" data-id="${rule.id}" type="button">Editar</button>${rule.active ? `<button class="button button-danger button-small" data-action="deactivate" data-id="${rule.id}" type="button">Desactivar</button>` : ""}</div></td>
    </tr>`).join("")
    : '<tr><td colspan="5" class="empty">No hay políticas registradas.</td></tr>'
}

async function loadRules() {
  try {
    rules = await API.get("/manufacturer-rules")
    renderRules()
  } catch (error) {
    UI.notice(ruleNotice, error.message, "error")
    rulesTable.innerHTML = '<tr><td colspan="5" class="empty">No fue posible cargar las políticas.</td></tr>'
  }
}

function resetRuleForm() {
  editingRuleId = null
  ruleForm.reset()
  ruleForm.elements.active.checked = true
  document.querySelector("#rule-form-title").textContent = "Registrar política"
  ruleSubmit.textContent = "Guardar política"
  ruleCancel.classList.add("hidden")
}

function startEditing(ruleId) {
  const rule = rules.find((item) => item.id === ruleId)
  if (!rule) return
  editingRuleId = rule.id
  ruleForm.elements.brand.value = rule.brand
  ruleForm.elements.interval_months.value = rule.interval_months
  ruleForm.elements.interval_km.value = rule.interval_km
  ruleForm.elements.active.checked = rule.active
  document.querySelector("#rule-form-title").textContent = `Editar política de ${rule.brand}`
  ruleSubmit.textContent = "Guardar cambios"
  ruleCancel.classList.remove("hidden")
  UI.clearNotice(ruleNotice)
  window.scrollTo({ top: 0, behavior: "smooth" })
}

ruleForm.addEventListener("submit", async (event) => {
  event.preventDefault()
  UI.clearNotice(ruleNotice)
  ruleSubmit.disabled = true
  try {
    const payload = rulePayload()
    if (!payload.brand || !Number.isInteger(payload.interval_months) || payload.interval_months <= 0 || !Number.isInteger(payload.interval_km) || payload.interval_km <= 0) {
      throw new Error("Completa la marca y usa intervalos enteros mayores que cero.")
    }
    if (editingRuleId) await API.put(`/manufacturer-rules/${editingRuleId}`, payload)
    else await API.post("/manufacturer-rules", payload)
    const message = editingRuleId ? "Política actualizada correctamente." : "Política registrada correctamente."
    resetRuleForm()
    await loadRules()
    UI.notice(ruleNotice, message, "success")
  } catch (error) {
    UI.notice(ruleNotice, error.message, "error")
  } finally {
    ruleSubmit.disabled = false
  }
})

rulesTable.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]")
  if (!button) return
  const ruleId = Number(button.dataset.id)
  if (button.dataset.action === "edit") {
    startEditing(ruleId)
    return
  }
  if (button.dataset.action === "deactivate") {
    const rule = rules.find((item) => item.id === ruleId)
    if (!window.confirm(`¿Desactivar la política de ${rule?.brand || "esta marca"}?`)) return
    try {
      await API.remove(`/manufacturer-rules/${ruleId}`)
      await loadRules()
      UI.notice(ruleNotice, "Política desactivada. Su historial se conserva.", "success")
    } catch (error) {
      UI.notice(ruleNotice, error.message, "error")
    }
  }
})

ruleCancel.addEventListener("click", () => {
  resetRuleForm()
  UI.clearNotice(ruleNotice)
})

loadRules()
