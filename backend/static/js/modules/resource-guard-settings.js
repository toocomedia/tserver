(() => {
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value).replace(/[&<>"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[char]));

  function renderStatus(status) {
    const row = $("resource-guard-status");
    if (!row) return;
    row.lastElementChild.textContent = `${status.state.replace("_", " ")} · ${status.ram_percent}% RAM · ${status.swap_percent}% swap`;
    $("resource_guard_mode").value = status.mode;
    $("resource_guard_limit").value = status.limit_percent;
    if ($("resource_guard_reserve")) $("resource_guard_reserve").value = status.protected_reserve_mb;
  }

  function renderPriorities(resources) {
    const body = $("resource-guard-priorities");
    if (!body) return;
    body.innerHTML = resources.map((resource) => `<tr><td class="table-title">${escapeHtml(resource.label)}</td><td>${escapeHtml(resource.type)}</td><td><select class="form-select" data-guard-priority data-type="${escapeHtml(resource.type)}" data-id="${escapeHtml(resource.id)}">${resource.priorities.map((priority) => `<option value="${priority}" ${priority === resource.priority ? "selected" : ""}>${priority}</option>`).join("")}</select></td></tr>`).join("") || '<tr><td colspan="3" class="text-muted">No managed resources found.</td></tr>';
  }

  async function load() {
    if (!$("tab-resource-guard")) return;
    try {
      const data = await panel.get("/api/settings/resource-guard");
      renderStatus(data.status);
      renderPriorities(data.resources || []);
    } catch (error) {
      const row = $("resource-guard-status");
      if (row) row.lastElementChild.textContent = "Unavailable";
    }
  }

  async function save() {
    const button = $("btn-save-resource-guard");
    const reserveEl = $("resource_guard_reserve");
    const payload = {
      mode: $("resource_guard_mode").value,
      memory_limit_percent: Number($("resource_guard_limit").value),
      protected_reserve_mb: reserveEl ? Number(reserveEl.value) : undefined,
    };
    button.disabled = true;
    try {
      const status = await panel.post("/api/settings/resource-guard", payload);
      renderStatus(status);
      toast("Resource Guard saved", "success");
    } catch (error) {
      toast(error.message || "Could not save Resource Guard", "danger");
    } finally {
      button.disabled = false;
    }
  }

  async function savePriority(event) {
    const select = event.target.closest("[data-guard-priority]");
    if (!select) return;
    select.disabled = true;
    try {
      await panel.post("/api/settings/resource-guard/priorities", {component_type: select.dataset.type, component_id: select.dataset.id, priority: select.value});
      toast("Priority saved", "success");
    } catch (error) {
      toast(error.message || "Could not save priority", "danger");
      load();
    } finally {
      select.disabled = false;
    }
  }

  function initRgSettings() {
    const btn = $("btn-save-resource-guard");
    const prio = $("resource-guard-priorities");
    if (!btn && !prio) return;
    if (btn) btn.onclick = save;
    if (prio) prio.onchange = savePriority;
    load();
  }

  document.addEventListener("app:init", initRgSettings);
  document.addEventListener("turbo:load", initRgSettings);
  initRgSettings();
})();
