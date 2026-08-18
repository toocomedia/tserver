/**
 * ai-helper-admin.js — AI Helper Admin & Provider Management Entry Point
 * Coordinates ProviderDrawerManager and PermissionsManager modules.
 */
import { ProviderDrawerManager } from "./modules/provider_drawer.js";
import { PermissionsManager } from "./modules/permissions.js";

// Global Window Helpers for inline HTML bindings and actions
window.AiHelperAdmin = ProviderDrawerManager;
window.PermissionsManager = PermissionsManager;

window.openAddDrawer = () => ProviderDrawerManager.openAddDrawer();
window.openEditDrawer = (id) => ProviderDrawerManager.openEditDrawer(id);
window.closeDrawer = () => ProviderDrawerManager.closeDrawer();

let currentInfoProviderId = null;

window.openInfoDrawer = (id) => {
  const row = document.getElementById(`provider-row-${id}`);
  if (!row) return;

  currentInfoProviderId = id;
  const name = row.getAttribute("data-name") || "Provider";
  const type = row.getAttribute("data-type") || "openai_compatible";
  const url = row.getAttribute("data-url") || "https://api.openai.com/v1";
  const model = row.getAttribute("data-model") || "";
  const rawModels = row.getAttribute("data-models") || "";
  const temp = row.getAttribute("data-temp") || "0.2";
  const tokens = row.getAttribute("data-tokens") || "4096";
  const rules = row.getAttribute("data-rules") || "";
  const isDefault = row.getAttribute("data-default") === "1";

  // Parse models list
  let modelsList = [];
  try {
    if (rawModels.startsWith("[") || rawModels.startsWith("{")) {
      const parsed = JSON.parse(rawModels);
      modelsList = Array.isArray(parsed) ? parsed : Object.keys(parsed);
    } else if (rawModels) {
      modelsList = rawModels.split(",").map((s) => s.trim()).filter(Boolean);
    }
  } catch (e) {
    if (rawModels) modelsList = rawModels.split(",").map((s) => s.trim()).filter(Boolean);
  }
  if (!modelsList.includes(model) && model) {
    modelsList.unshift(model);
  }

  // Populate info modal
  const nameEl = document.getElementById("info-provider-name");
  const defBadge = document.getElementById("info-provider-default");
  const statusBadge = document.getElementById("info-provider-status");
  const typeEl = document.getElementById("info-provider-type");
  const modelEl = document.getElementById("info-provider-active-model");
  const tempEl = document.getElementById("info-provider-temp");
  const tokensEl = document.getElementById("info-provider-tokens");
  const urlEl = document.getElementById("info-provider-url");
  const countBadge = document.getElementById("info-models-count");
  const container = document.getElementById("info-models-container");
  const filterInput = document.getElementById("info-filter-models");
  const rulesEl = document.getElementById("info-custom-rules");
  const editBtn = document.getElementById("info-btn-edit");

  if (nameEl) nameEl.textContent = name;
  if (defBadge) defBadge.style.display = isDefault ? "inline-flex" : "none";
  if (typeEl) typeEl.textContent = type === "anthropic" ? "Anthropic (/messages)" : "OpenAI Compatible (/chat/completions)";
  if (modelEl) modelEl.textContent = model || "None";
  if (tempEl) tempEl.textContent = temp;
  if (tokensEl) tokensEl.textContent = tokens;
  if (urlEl) urlEl.textContent = url;
  if (countBadge) countBadge.textContent = `${modelsList.length} model${modelsList.length === 1 ? "" : "s"}`;
  if (rulesEl) rulesEl.textContent = rules.trim() || "No custom prompt rules configured.";

  // Clone status from table row
  const rowStatusBadge = row.querySelector("td:nth-child(5) .badge-minimal") || row.querySelector("td:nth-child(5) .badge");
  if (statusBadge && rowStatusBadge) {
    statusBadge.className = rowStatusBadge.className;
    statusBadge.innerHTML = rowStatusBadge.innerHTML;
  }

  // Render models list function
  const renderModels = (filterText = "") => {
    if (!container) return;
    const filterLower = filterText.toLowerCase().trim();
    const filtered = modelsList.filter((m) => !filterLower || m.toLowerCase().includes(filterLower));

    if (filtered.length === 0) {
      container.innerHTML = `<span class="text-muted text-xs" style="padding: 6px 8px;">No matching models</span>`;
      return;
    }

    container.innerHTML = filtered
      .map((m) => {
        const isSelected = m === model;
        return `
          <div class="info-model-row" style="display: flex; align-items: center; justify-content: space-between; padding: 5px 8px; border-radius: 4px; background: ${
            isSelected ? "var(--color-surface)" : "transparent"
          }; font-family: var(--font-mono, monospace); font-size: 11.5px;">
            <div style="display: flex; align-items: center; gap: 6px; min-width: 0;">
              <span style="color: ${isSelected ? "var(--color-accent)" : "var(--color-muted)"}; font-size: 11px;">${isSelected ? "★" : "•"}</span>
              <span class="text-truncate" style="color: ${isSelected ? "var(--color-text)" : "var(--color-muted)"}; font-weight: ${isSelected ? "600" : "400"};">${m}</span>
            </div>
            ${isSelected ? '<span class="badge-minimal badge-minimal--active" style="font-size: 9px; padding: 1px 5px;">Active</span>' : ""}
          </div>
        `;
      })
      .join("");
  };

  if (filterInput) {
    filterInput.value = "";
    filterInput.oninput = (e) => renderModels(e.target.value);
  }
  renderModels();

  if (editBtn) {
    editBtn.onclick = () => {
      window.closeInfoDrawer();
      window.openEditDrawer(id);
    };
  }

  const modal = document.getElementById("provider-info-drawer-modal");
  if (modal) {
    modal.classList.remove("hidden");
    if (typeof lucide !== "undefined") lucide.createIcons();
  }
};

window.closeInfoDrawer = () => {
  const modal = document.getElementById("provider-info-drawer-modal");
  if (modal) modal.classList.add("hidden");
  currentInfoProviderId = null;
};

window.openPermissionsDrawer = () => PermissionsManager.openDrawer();
window.closePermissionsDrawer = () => PermissionsManager.closeDrawer();
window.updateAccessModeUI = () => PermissionsManager.updateAccessModeUI();
window.savePermissions = (e) => PermissionsManager.savePermissions(e);
window.refreshAuditLogs = () => PermissionsManager.refreshAuditLogs();

window.testProvider = (providerId, btn) => {
  const origHtml = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = "...";
  const csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";

  fetch(`/plugins/ai_helper/${providerId}/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
  })
    .then((res) => res.json())
    .then((data) => {
      btn.disabled = false;
      btn.innerHTML = origHtml;
      if (typeof lucide !== "undefined") lucide.createIcons();
      if (data.success) {
        alert(`Connection successful (${data.latency_ms}ms)`);
        window.location.reload();
      } else {
        alert(`Connection failed: ${data.error || "Unknown error"}`);
      }
    })
    .catch((err) => {
      btn.disabled = false;
      btn.innerHTML = origHtml;
      if (typeof lucide !== "undefined") lucide.createIcons();
      alert(`Network error: ${err.message}`);
    });
};

window.setDefaultProvider = (providerId) => {
  const csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";
  fetch(`/plugins/ai_helper/${providerId}/set-default`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
  })
    .then((res) => res.json())
    .then((data) => {
      if (data.status === "ok") {
        window.location.reload();
      } else {
        alert(`Failed to set default: ${data.message || "Unknown error"}`);
      }
    })
    .catch((err) => alert(`Error: ${err.message}`));
};

window.deleteProvider = (providerId, providerName) => {
  if (!confirm(`Are you sure you want to delete '${providerName}'?`)) return;
  const csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";

  fetch(`/plugins/ai_helper/${providerId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
  })
    .then((res) => res.json())
    .then((data) => {
      if (data.status === "ok") {
        window.location.reload();
      } else {
        alert(`Failed to delete provider: ${data.message || "Unknown error"}`);
      }
    })
    .catch((err) => alert(`Error deleting provider: ${err.message}`));
};

// Initialize Modules on DOM Ready
function initAll() {
  ProviderDrawerManager.init();
  PermissionsManager.init();

  const btnAdd = document.getElementById("btn-open-add");
  if (btnAdd) {
    btnAdd.addEventListener("click", () => ProviderDrawerManager.openAddDrawer());
  }

  const btnPerm = document.getElementById("btn-open-permissions");
  if (btnPerm) {
    btnPerm.addEventListener("click", () => PermissionsManager.openDrawer());
  }

  const infoModal = document.getElementById("provider-info-drawer-modal");
  if (infoModal) {
    infoModal.addEventListener("click", (e) => {
      if (e.target === infoModal) window.closeInfoDrawer();
    });
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initAll);
} else {
  initAll();
}
