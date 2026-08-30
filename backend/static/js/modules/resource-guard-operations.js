(() => {
  const ACTIVE_STATUSES = new Set(["queued", "running", "cancelling"]);
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));

  function formatMb(value) {
    return Number.isFinite(value) ? `${value} MB` : "—";
  }

  function formatStarted(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? "—" : date.toLocaleString();
  }

  function statusCell(operation) {
    const tone = operation.status === "cancelling" ? "warning" : "muted";
    const queue = operation.status === "queued" && operation.queue_position
      ? ` #${operation.queue_position}` : "";
    return `<span class="status-badge status-badge--${tone}">${escapeHtml(operation.status)}${queue}</span>`;
  }

  function render(operations) {
    const body = $("resource-guard-operations");
    if (!body) return;
    if (!operations.length) {
      body.innerHTML = '<tr><td colspan="7" class="text-muted">No active operations.</td></tr>';
      return;
    }
    body.innerHTML = operations.map((operation) => `<tr>
      <td class="table-title">${escapeHtml(operation.label)}</td>
      <td>${escapeHtml(operation.profile)}</td>
      <td>${statusCell(operation)}</td>
      <td>${formatMb(operation.reserved_mb)}</td>
      <td>${formatMb(operation.peak_ram_mb)}</td>
      <td>${formatStarted(operation.started_at)}</td>
      <td><button type="button" class="btn btn--danger btn--sm" data-operation-cancel="${operation.id}" ${operation.status === "cancelling" ? "disabled" : ""}>${operation.status === "cancelling" ? "Cancelling" : "Cancel"}</button></td>
    </tr>`).join("");
  }

  async function loadOperations() {
    if (!$("tab-resource-guard")?.classList.contains("active")) return;
    try {
      const data = await panel.get("/api/resource-guard/operations");
      render((data.operations || []).filter((item) => ACTIVE_STATUSES.has(item.status)));
    } catch (error) {
      const body = $("resource-guard-operations");
      if (body) body.innerHTML = '<tr><td colspan="7" class="text-muted">Operations are unavailable.</td></tr>';
    }
  }

  async function cancelOperation(button) {
    const operationId = button.dataset.operationCancel;
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    try {
      await panel.post(`/api/resource-guard/operations/${operationId}/cancel`);
      toast("Cancellation requested", "success");
      await loadOperations();
    } catch (error) {
      toast(error.message || "Could not cancel operation", "danger");
      button.disabled = false;
    } finally {
      button.classList.remove("is-loading");
      button.removeAttribute("aria-busy");
    }
  }

  document.addEventListener("app:init", () => {
    $("resource-guard-operations")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-operation-cancel]");
      if (!button || button.disabled) return;
      confirmAction("Cancel this operation? Running work will stop as soon as possible.", () => cancelOperation(button), {
        title: "Cancel Operation", okLabel: "Cancel operation", danger: true,
      });
    });
    document.addEventListener("click", (event) => {
      if (event.target.closest('[data-tab="tab-resource-guard"]')) loadOperations();
    });
    if ($("tab-resource-guard")?.classList.contains("active")) loadOperations();
    window.setInterval(loadOperations, 5000);
  });
})();
