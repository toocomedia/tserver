/**
 * JS Module for PHP Site Detail Page (detail.html)
 */
import { showOperationModal, pollOperation } from "./php_sites_operations.js";

function showPromptModal(title, message, expectedText, confirmInstruction, isPassword, extraHtml, onConfirm) {
  document.getElementById('prompt-modal-title').textContent = title;
  document.getElementById('prompt-modal-message').innerHTML = message;
  
  const label = document.getElementById('prompt-modal-label');
  if (confirmInstruction) {
    label.innerHTML = confirmInstruction;
  } else if (expectedText) {
    label.innerHTML = `Type <code class="mono">${expectedText}</code> to confirm`;
  } else {
    label.innerHTML = isPassword ? "Enter password:" : "Enter value:";
  }
  
  const input = document.getElementById('prompt-modal-input');
  input.type = isPassword ? 'password' : 'text';
  input.value = '';
  
  const extra = document.getElementById('prompt-modal-extra');
  if (extraHtml) {
    extra.innerHTML = extraHtml;
    extra.style.display = 'block';
  } else {
    extra.style.display = 'none';
  }
  
  const btn = document.getElementById('btn-prompt-modal-confirm');
  btn.onclick = () => {
    const val = input.value.trim();
    if (!val) return;
    if (expectedText && val.toUpperCase() !== expectedText.toUpperCase()) {
      if (typeof window.toast === 'function') window.toast(`Input must match exactly: ${expectedText}`, 'danger');
      else alert(`Input must match exactly: ${expectedText}`);
      return;
    }
    closeModal('prompt-modal');
    onConfirm(expectedText ? expectedText : val);
  };
  
  openModal('prompt-modal');
  setTimeout(() => input.focus(), 100);
}

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
  const domainName = container.dataset.domainName || `site-${siteId}`;
  const csrfToken = document.querySelector('input[name="csrf_token"]')?.value || "";

  // 1. Tab Navigation
  const tabs = container.querySelectorAll(".tabs-nav__btn");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");

      const target = tab.dataset.tab;
      container.querySelectorAll(".tabs-panel").forEach((pane) => {
        if (pane.id === `tab-${target}`) {
          pane.classList.add("is-active");
        } else {
          pane.classList.remove("is-active");
        }
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
      .catch((err) => {
        if (typeof window.toast === "function") {
          window.toast(parseErrorMessage(err), "danger");
        } else {
          alert(parseErrorMessage(err));
        }
      });
  }

  // 2. Health Probes
  const refreshHealthBtn = document.getElementById("btn-refresh-health");
  async function fetchHealth() {
    try {
      const res = await fetch(`/api/php-sites/sites/${siteId}/health`);
      if (!res.ok) return;
      const data = await res.json();
      document.getElementById("probe-socket").innerHTML = data.socket ? '<span class="status-badge status-badge--success">OK</span>' : '<span class="status-badge status-badge--danger">DOWN</span>';
      document.getElementById("probe-nginx").innerHTML = data.nginx ? '<span class="status-badge status-badge--success">OK</span>' : '<span class="status-badge status-badge--danger">ERROR</span>';
      document.getElementById("probe-http").innerHTML = data.http ? '<span class="status-badge status-badge--success">OK</span>' : '<span class="status-badge status-badge--danger">DOWN</span>';
      if (document.getElementById("probe-mariadb")) {
        document.getElementById("probe-mariadb").innerHTML = data.mariadb ? '<span class="status-badge status-badge--success">OK</span>' : '<span class="status-badge status-badge--muted">N/A</span>';
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
    if (typeof confirmAction === 'function') {
      confirmAction("Rotate database password and update PHP-FPM environment?", async () => {
        try {
          const data = await apiCall(`/api/php-sites/sites/${siteId}/database/rotate`);
          document.getElementById("db-revealed-pwd").textContent = data.password;
          document.getElementById("db-credentials-box").style.display = "block";
          if (typeof window.toast === 'function') window.toast("Database password rotated successfully.", 'success');
        } catch (err) { if (typeof window.toast === 'function') window.toast(parseErrorMessage(err), 'danger'); else alert(parseErrorMessage(err)); }
      });
    }
  });

  document.getElementById("btn-create-db")?.addEventListener("click", async () => {
    try {
      const data = await apiCall(`/api/php-sites/sites/${siteId}/database`, "POST", { install_missing_extension: true });
      alert(`Database ${data.database} created!`);
      window.location.reload();
    } catch (err) { alert(parseErrorMessage(err)); }
  });

  document.getElementById("btn-delete-db")?.addEventListener("click", () => {
    showPromptModal(
      "Delete Database",
      "Permanently delete the local MariaDB database and user?",
      "DELETE DATABASE",
      `Type <code class="mono">DELETE DATABASE</code> to confirm deletion.`,
      false,
      null,
      async (confirmText) => {
        try {
          await apiCall(`/api/php-sites/sites/${siteId}/database`, "DELETE", { confirmation: confirmText });
          if (typeof window.toast === 'function') window.toast("Database deleted.", 'success');
          setTimeout(() => window.location.reload(), 1000);
        } catch (err) { if (typeof window.toast === 'function') window.toast(parseErrorMessage(err), 'danger'); else alert(parseErrorMessage(err)); }
      }
    );
  });

  // 5. WordPress & SSL
  document.getElementById("btn-wp-retry")?.addEventListener("click", () => {
    showPromptModal(
      "Retry WordPress Setup",
      "Enter a one-time WordPress admin password to retry setup (at least 12 characters).",
      null,
      null,
      true,
      null,
      (pwd) => {
        if (pwd.length < 12) {
          if (typeof window.toast === 'function') window.toast("Password must be at least 12 characters.", 'danger');
          else alert("Password must be at least 12 characters.");
          return;
        }
        handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/wordpress/retry`, "POST", { admin_password: pwd, install_missing_extensions: true }), "Retrying WordPress Setup...");
      }
    );
  });

  document.getElementById("btn-ssl-issue")?.addEventListener("click", () => {
    const incWww = document.getElementById("ssl-include-www")?.checked || false;
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/ssl/issue`, "POST", { include_www: incWww }), "Issuing SSL Certificate...");
  });

  document.getElementById("btn-ssl-renew")?.addEventListener("click", () => {
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/ssl/renew`), "Renewing SSL Certificate...");
  });

  document.getElementById("btn-ssl-revoke")?.addEventListener("click", () => {
    const expected = `REVOKE ${domainName}`;
    showPromptModal(
      "Revoke SSL",
      "This will revoke the active Let's Encrypt certificate and revert the site to HTTP.",
      expected,
      `Type <code class="mono">${expected}</code> to confirm SSL revocation.`,
      false,
      null,
      (confirmText) => {
        handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/ssl`, "DELETE", { confirmation: confirmText }), "Revoking SSL...");
      }
    );
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
    const expected = `ARCHIVE ${domainName}`;
    showPromptModal(
      "Archive Website",
      "This will stop the PHP-FPM pool and Nginx site while keeping all data intact.",
      expected,
      `Type <code class="mono">${expected}</code> to confirm archiving.`,
      false,
      null,
      (confirmText) => {
        handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/archive`, "POST", { confirmation: confirmText }), "Archiving Website...");
      }
    );
  });

  document.getElementById("btn-restore-site")?.addEventListener("click", () => {
    handleAsyncOperation(apiCall(`/api/php-sites/sites/${siteId}/restore`), "Restoring Website...");
  });

  document.getElementById("btn-delete-site-perm")?.addEventListener("click", () => {
    const expected = `DELETE ${domainName}`;
    const extraHtml = `
      <label class="form-check" style="display: flex; align-items: center; gap: 8px;">
        <input type="checkbox" id="delete-site-db-check" checked>
        <span>Also drop the attached local MariaDB database</span>
      </label>
    `;
    showPromptModal(
      "Delete Website Permanently",
      "This will permanently delete the PHP-FPM pool, webroot directory, and Nginx configuration. This action cannot be undone.",
      expected,
      `Type <code class="mono">${expected}</code> to confirm permanent removal.`,
      false,
      extraHtml,
      async (confirmText) => {
        const delDb = document.getElementById('delete-site-db-check')?.checked || false;
        try {
          await apiCall(`/api/php-sites/sites/${siteId}`, "DELETE", { confirmation: confirmText, delete_database: delDb });
          if (typeof window.toast === 'function') window.toast("Website deleted.", 'success');
          setTimeout(() => window.location.href = "/php-sites/", 1000);
        } catch (err) { if (typeof window.toast === 'function') window.toast(parseErrorMessage(err), 'danger'); else alert(parseErrorMessage(err)); }
      }
    );
  });
});
