(() => {
  let inventory = [];
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const typeLabels = { build_dir: "Build dir", build_workspace: "Build workspace", dangling_image: "Dangling image", unused_image: "Unused image", build_cache: "Builder cache", old_log: "Old log" };

  function formatMb(value) {
    return `${Number(value || 0).toFixed(1)} MB`;
  }

  function formatAge(item) {
    return item.age_days > 0 ? `${Math.round(item.age_days)} days ago` : "Unknown";
  }

  function selectedItems() {
    return inventory.filter((item) => $("resource-guard-disk-item-" + item.item_id)?.checked);
  }

  function updateSelection() {
    const selected = selectedItems();
    const selectedMb = selected.reduce((total, item) => total + Number(item.size_mb || 0), 0);
    const recoverableMb = inventory.filter((item) => !item.protected)
      .reduce((total, item) => total + Number(item.size_mb || 0), 0);
    $("resource-guard-disk-total").textContent = `${formatMb(selectedMb)} selected of ${formatMb(recoverableMb)} reclaimable`;
    $("btn-clean-resource-guard-disk").disabled = selected.length === 0;
    const selectable = inventory.filter((item) => !item.protected);
    const selectAll = $("resource-guard-disk-select-all");
    if (selectAll) selectAll.checked = selectable.length > 0 && selected.length === selectable.length;
  }

  function renderInventory(items) {
    inventory = items;
    const body = $("resource-guard-disk-items");
    $("resource-guard-disk-results").hidden = false;
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="6" class="text-muted">No reclaimable space found.</td></tr>';
      updateSelection();
      return;
    }
    body.innerHTML = items.map((item) => `<tr>
      <td>${typeLabels[item.type] || escapeHtml(item.type)}</td>
      <td class="table-title">${escapeHtml(item.path)}</td>
      <td>${formatMb(item.size_mb)}</td>
      <td>${formatAge(item)}</td>
      <td>${item.protected ? `<span class="status-badge status-badge--warning" title="${escapeHtml(item.protect_reason)}">Protected</span>` : "—"}</td>
      <td><input type="checkbox" id="resource-guard-disk-item-${escapeHtml(item.item_id)}" data-disk-item="${escapeHtml(item.item_id)}" ${item.protected ? "disabled" : ""} aria-label="Select ${escapeHtml(item.path)}"></td>
    </tr>`).join("");
    updateSelection();
  }

  async function scan() {
    const button = $("btn-scan-resource-guard-disk");
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    $("resource-guard-disk-result").textContent = "";
    try {
      const data = await panel.get("/api/resource-guard/disk-inventory");
      renderInventory([...(data.deletable || []), ...(data.protected || [])]);
    } catch (error) {
      toast(error.message || "Could not scan disk space", "danger");
    } finally {
      button.disabled = false;
      button.classList.remove("is-loading");
      button.removeAttribute("aria-busy");
    }
  }

  async function cleanup() {
    const button = $("btn-clean-resource-guard-disk");
    const includeIds = selectedItems().map((item) => item.item_id);
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    try {
      const result = await panel.post("/api/resource-guard/disk-cleanup", { include_ids: includeIds });
      const errors = result.errors?.length ? ` ${result.errors.length} item(s) could not be removed.` : "";
      $("resource-guard-disk-result").textContent = `Freed ${formatMb(result.freed_mb)}.${errors}`;
      toast(`Freed ${formatMb(result.freed_mb)}`, "success");
      await scan();
    } catch (error) {
      toast(error.message || "Could not free disk space", "danger");
    } finally {
      button.classList.remove("is-loading");
      button.removeAttribute("aria-busy");
      updateSelection();
    }
  }

  document.addEventListener("app:init", () => {
    $("btn-scan-resource-guard-disk")?.addEventListener("click", scan);
    $("resource-guard-disk-items")?.addEventListener("change", updateSelection);
    $("resource-guard-disk-select-all")?.addEventListener("change", (event) => {
      inventory.filter((item) => !item.protected).forEach((item) => {
        $("resource-guard-disk-item-" + item.item_id).checked = event.target.checked;
      });
      updateSelection();
    });
    $("btn-clean-resource-guard-disk")?.addEventListener("click", () => {
      const count = selectedItems().length;
      if (!count) return;
      confirmAction(`Free disk space from ${count} selected item(s)? This cannot be undone.`, cleanup, {
        title: "Free Disk Space", okLabel: "Free selected space", danger: true,
      });
    });
  });
})();
