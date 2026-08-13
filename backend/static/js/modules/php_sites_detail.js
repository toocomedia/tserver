/**
 * JS Module for PHP Site Detail Page (detail.html)
 */
import { showOperationModal, pollOperation } from "./php_sites_operations.js";

function parseErrorMessage(err) {
  if (!err) return "An unexpected error occurred.";
  if (typeof err === "string") return err;
  
  if (err.detail !== undefined && err.detail !== null) {
    return parseErrorMessage(err.detail);
  }
  if (err.error !== undefined && err.error !== null) {
    return parseErrorMessage(err.error);
  }
  if (typeof err.message === "string") return err.message;
  if (typeof err.reason === "string") return err.reason;

  if (Array.isArray(err)) {
    return err.map(item => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const field = Array.isArray(item.loc) ? item.loc.filter(x => x !== "body").join(".") : "";
        const msg = item.msg || item.message || item.detail || item.reason || JSON.stringify(item);
        return field ? `${field}: ${msg}` : msg;
      }
      return String(item);
    }).join("\n");
  }

  try {
    return JSON.stringify(err, null, 2);
  } catch (_) {
    return String(err);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const container = document.querySelector(".php-site-detail-page");
  if (!container) return;

  const siteId = container.dataset.siteId;
  const csrfToken = document.querySelector('input[name="csrf_token"]')?.value || "";

  // 1. Tab Navigation
  const tabs = container.querySelectorAll(".tab-item");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("tab-item--active"));
      tab.classList.add("tab-item--active");

      const target = tab.dataset.tab;
      container.querySelectorAll(".tab-pane").forEach((pane) => {
        pane.style.display = pane.id === `tab-${target}` ? "block" : "none";
      });
      if (target === "logs") fetchLogs();
    });
  });

  // Helper for API calls with CSRF
  async function apiCall(url, method = "POST", body = null) {
    const opts = {
      method,
      headers: { "X-CSRF-Token": csrfToken },
    };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(parseErrorMessage(data));
    return data;
  }

  function handleAsyncOperation(promise, title) {
    showOperationModal(title);
    promise
      .then((data) => {
        pollOperation(data.operation_id, () => window.location.reload());
      })
      .catch((err) => alert(parseErrorMessage(err)));
  }

  // 2. Health Probes
  const refreshHealthBtn = document.getElementById("btn-refresh-health");
  async function fetchHealth() {
    try {
      const res = await fetch(`/api/php-sites/sites/${siteId}/health`);
      if (!res.ok) return;
      const data = await res.json();
      document.getElementById("probe-socket").innerHTML = data.socket ? '<span class="text-success">OK</span>' : '<span class="text-danger">DOWN</span>';
      document.getElementById("probe-nginx").innerHTML = data.nginx ? '<span class="text-success">OK</span>' : '<span class="text-danger">ERROR</span>';
      document.getElementById("probe-http").innerHTML = data.http ? '<span class="text-success">OK</span>' : '<span class="text-danger">DOWN</span>';
      if (document.getElementById("probe-mariadb")) {
        document.getElementById("probe-mariadb").innerHTML = data.mariadb ? '<span class="text-success">OK</span>' : '<span class="text-muted">N/A</span>';
      }
    } catch (_) {}
  }
  if (refreshHealthBtn) refreshHealthBtn.addEventListener("click", fetchHealth);
  fetchHealth();

  // 3. Runtime & Document Root
  document.getElementById("form-change-runtime")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const ver = document.getElementById("select-runtime-version").value;
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/runtime`, "POST", { php_version: ver }), "Changing PHP Runtime...");
  });

  document.getElementById("form-change-docroot")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const root = document.getElementById("input-docroot").value.trim();
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/document-root`, "PATCH", { document_root: root }), "Updating Document Root...");
  });

  // 4. Database Actions
  document.getElementById("btn-reveal-db")?.addEventListener("click", async () => {
    try {
      const data = await apiCall(`/api/php-sites/sites/${siteId}/database/reveal`);
      document.getElementById("db-revealed-pwd").textContent = data.password;
      document.getElementById("db-credentials-box").style.display = "block";
    } catch (err) { alert(parseErrorMessage(err)); }
  });

  document.getElementById("btn-rotate-db")?.addEventListener("click", async () => {
    if (!confirm("Rotate database password and update PHP-FPM environment?")) return;
    try {
      const data = await apiCall(`/api/php-sites/sites/${siteId}/database/rotate`);
      document.getElementById("db-revealed-pwd").textContent = data.password;
      document.getElementById("db-credentials-box").style.display = "block";
      alert("Database password rotated successfully.");
    } catch (err) { alert(parseErrorMessage(err)); }
  });

  document.getElementById("btn-create-db")?.addEventListener("click", async () => {
    try {
      const data = await apiCall(`/api/php-sites/sites/${siteId}/database`, "POST", { install_missing_extension: true });
      alert(`Database ${data.database} created!`);
      window.location.reload();
    } catch (err) { alert(parseErrorMessage(err)); }
  });

  document.getElementById("btn-delete-db")?.addEventListener("click", async () => {
    const name = prompt("Type DELETE DATABASE to confirm database deletion:");
    if (!name) return;
    try {
      await apiCall(`/api/php-sites/sites/${siteId}/database`, "DELETE", { confirmation: name });
      alert("Database deleted.");
      window.location.reload();
    } catch (err) { alert(parseErrorMessage(err)); }
  });

  // 5. WordPress & SSL
  document.getElementById("btn-wp-retry")?.addEventListener("click", () => {
    const pwd = prompt("Enter one-time WordPress admin password (at least 12 characters):");
    if (!pwd) return;
    if (pwd.length < 12) {
      alert("WordPress administrator password must be at least 12 characters long.");
      return;
    }
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/wordpress/retry`, "POST", { admin_password: pwd, install_missing_extensions: true }), "Retrying WordPress Setup...");
  });

  document.getElementById("btn-ssl-issue")?.addEventListener("click", () => {
    const incWww = document.getElementById("ssl-include-www")?.checked || false;
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/ssl/issue`, "POST", { include_www: incWww }), "Issuing SSL Certificate...");
  });

  document.getElementById("btn-ssl-renew")?.addEventListener("click", () => {
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/ssl/renew`), "Renewing SSL Certificate...");
  });

  document.getElementById("btn-ssl-revoke")?.addEventListener("click", () => {
    const name = prompt("Type REVOKE {domain} to confirm SSL revocation:");
    if (!name) return;
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/ssl`, "DELETE", { confirmation: name }), "Revoking SSL...");
  });

  // 6. Logs Streaming
  async function fetchLogs() {
    const stream = document.getElementById("log-stream-select")?.value || "access";
    const lines = document.getElementById("log-lines-select")?.value || "200";
    const box = document.getElementById("log-output-content");
    if (!box) return;
    box.textContent = "Loading logs...";
    try {
      const res = await fetch(`/api/php-sites/sites/${siteId}/logs?stream=${stream}&lines=${lines}`);
      const data = await res.json();
      box.textContent = (data.lines || []).join("\n") || "Log file is empty.";
    } catch (err) { box.textContent = "Failed to load logs: " + parseErrorMessage(err); }
  }
  document.getElementById("btn-fetch-logs")?.addEventListener("click", fetchLogs);

  // 7. Control & Danger Actions
  document.getElementById("btn-toggle-control")?.addEventListener("click", (e) => {
    const act = e.target.dataset.action;
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/control`, "POST", { action: act }), `${act.toUpperCase()} Website...`);
  });

  document.getElementById("btn-repair-site")?.addEventListener("click", () => {
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/repair`), "Repairing Website...");
  });

  document.getElementById("btn-archive-site")?.addEventListener("click", () => {
    const name = prompt("Type ARCHIVE {domain} to confirm archiving:");
    if (!name) return;
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/archive`, "POST", { confirmation: name }), "Archiving Website...");
  });

  document.getElementById("btn-restore-site")?.addEventListener("click", () => {
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/restore`), "Restoring Website...");
  });

  document.getElementById("btn-delete-site-perm")?.addEventListener("click", async () => {
    const name = prompt("Type DELETE {domain} to permanently delete website:");
    if (!name) return;
    const delDb = confirm("Drop local MariaDB database as well?");
    try {
      await apiCall(`/api/php-sites/sites/${siteId}`, "DELETE", { confirmation: name, delete_database: delDb });
      alert("Website deleted.");
      window.location.href = "/php-sites/";
    } catch (err) { alert(parseErrorMessage(err)); }
  });
});
