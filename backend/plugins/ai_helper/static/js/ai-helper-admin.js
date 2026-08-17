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

window.openPermissionsDrawer = () => PermissionsManager.openDrawer();
window.closePermissionsDrawer = () => PermissionsManager.closeDrawer();
window.updateAccessModeUI = () => PermissionsManager.updateAccessModeUI();
window.savePermissions = () => PermissionsManager.savePermissions();
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

  if (window.location.search.includes("open=create")) {
    ProviderDrawerManager.openAddDrawer();
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initAll);
} else {
  initAll();
}
