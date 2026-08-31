async function loadResult() {
  const target = document.querySelector("#result-detail"); const id = new URLSearchParams(window.location.search).get("id")
  if (!id) { target.className = "notice error show"; target.textContent = "Selecciona una validación desde el historial para ver su detalle."; return }
  try { const result = await API.get(`/validations/${id}`); target.className = ""; target.innerHTML = resultMarkup(result, true) } catch (error) { target.className = "notice error show"; target.textContent = error.message }
}
loadResult()

