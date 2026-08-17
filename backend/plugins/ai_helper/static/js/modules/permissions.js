/**
 * modules/permissions.js — Security & Permissions Modal Controller
 */
import { MultiSelectPicker } from "./multiselect.js";

export const PermissionsManager = {
  pickers: {},
  cachedResources: null,

  init() {
    this.pickers.domains = new MultiSelectPicker({ key: "domains" });
    this.pickers.apps = new MultiSelectPicker({ key: "apps" });
    this.pickers.databases = new MultiSelectPicker({ key: "databases" });
    this.pickers.file_targets = new MultiSelectPicker({ key: "file_targets" });

    if (window.INITIAL_RESOURCES) this.populateResources(window.INITIAL_RESOURCES);

    document.querySelectorAll(".perm-tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetId = btn.getAttribute("data-target");
        document.querySelectorAll(".perm-tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".perm-tab-pane").forEach((p) => (p.style.display = "none"));
        btn.classList.add("active");
        const pane = document.getElementById(targetId);
        if (pane) pane.style.display = "block";
        if (typeof lucide !== "undefined") lucide.createIcons();
      });
    });

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".perm-multiselect")) {
        document.querySelectorAll(".perm-multiselect.open").forEach((el) => el.classList.remove("open"));
      }
    });

    this.fetchResources();
  },

  populateResources(res) {
    if (!res) return;
    this.cachedResources = res;
    if (this.pickers.domains) this.pickers.domains.setOptions(res.domains || []);
    if (this.pickers.apps) this.pickers.apps.setOptions(res.apps || []);
    if (this.pickers.databases) this.pickers.databases.setOptions(res.databases || []);
    if (this.pickers.file_targets) this.pickers.file_targets.setOptions(res.file_targets || []);
  },

  fetchResources() {
    fetch("/plugins/ai_helper/api/resources")
      .then((res) => res.json())
      .then((data) => {
        if (data.status === "ok" && data.resources) this.populateResources(data.resources);
      })
      .catch((err) => console.debug("Resource discovery error:", err));
  },

  updateAccessModeUI() {
    const selectedRadio = document.querySelector('input[name="global_mode"]:checked');
    const mode = selectedRadio ? selectedRadio.value : "full_read_only";

    document.querySelectorAll("[data-mode-card]").forEach((card) => {
      const isSelected = card.getAttribute("data-mode-card") === mode;
      card.classList.toggle("settings-choice--active", isSelected);
      const radio = card.querySelector('input[type="radio"]');
      if (radio) radio.checked = isSelected;
    });

    const noticeEl = document.getElementById("scope-mode-notice");
    const listsWrap = document.getElementById("scope-lists-wrap");
    const tabWhitelistBtn = document.querySelector('.perm-tab-btn[data-target="perm-tab-scope"]');

    if (noticeEl) {
      if (mode === "full_read_only") {
        noticeEl.className = "alert alert--neutral mb-md text-xs";
        noticeEl.style.display = "block";
        noticeEl.textContent = "Full Access is active. The Whitelist below is bypassed.";
      } else if (mode === "disabled") {
        noticeEl.className = "alert alert--danger mb-md text-xs";
        noticeEl.style.display = "block";
        noticeEl.textContent = "AI Access is Blocked. All tools and whitelists are disabled.";
      } else {
        noticeEl.className = "alert alert--ok mb-md text-xs";
        noticeEl.style.display = "block";
        noticeEl.textContent = "Granular Whitelist is active. Only selected items below are accessible.";
      }
    }

    if (listsWrap) {
      listsWrap.style.opacity = (mode === "selective") ? "1" : "0.45";
      listsWrap.style.pointerEvents = (mode === "selective") ? "auto" : "none";
    }

    if (tabWhitelistBtn) {
      tabWhitelistBtn.textContent = (mode === "selective") ? "Whitelist (Active)" : "Whitelist";
    }
  },

  openDrawer() {
    const modal = document.getElementById("permissions-drawer-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    document.body.classList.add("modal-open");
    if (window.INITIAL_RESOURCES && !this.cachedResources) this.populateResources(window.INITIAL_RESOURCES);
    this.fetchResources();
    this.updateAccessModeUI();
    if (typeof lucide !== "undefined") lucide.createIcons();
  },

  closeDrawer() {
    const modal = document.getElementById("permissions-drawer-modal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.classList.remove("modal-open");
  },

  refreshAuditLogs() {
    const container = document.getElementById("audit-log-list");
    if (!container) return;

    fetch("/plugins/ai_helper/api/audit-logs?limit=30")
      .then((res) => res.json())
      .then((data) => {
        if (data.status === "ok" && data.logs) {
          if (data.logs.length === 0) {
            container.innerHTML = '<div class="p-md text-center text-muted text-xs"><p class="m-0">No tool execution audit logs recorded yet.</p></div>';
            return;
          }
          let html = '<div class="table-wrap m-0"><table class="table table--compact text-xs" style="margin: 0;"><thead><tr><th style="width: 80px;">Status</th><th style="width: 140px;">Tool</th><th>Reason / Target</th><th style="width: 80px; text-align: right;">Time</th></tr></thead><tbody>';
          data.logs.forEach((log) => {
            const badgeClass = log.status === "allowed" ? "badge--ok" : "badge--error";
            const time = log.timestamp ? log.timestamp.substring(11, 19) : "";
            html += `<tr><td><span class="badge ${badgeClass}" style="font-size: 9px; padding: 2px 5px;">${(log.status || "").toUpperCase()}</span></td><td class="font-mono font-bold">${log.tool || ""}</td><td class="text-muted">${log.reason || ""}</td><td class="text-muted text-right font-mono" style="font-size: 10px;">${time}</td></tr>`;
          });
          html += "</tbody></table></div>";
          container.innerHTML = html;
        }
      })
      .catch((err) => console.debug("Failed to refresh audit logs:", err));
  },

  savePermissions(e) {
    if (e && e.preventDefault) e.preventDefault();
    const form = document.getElementById("ai-permissions-form");
    if (!form) return;

    const btn = document.getElementById("btn-save-permissions");
    const statusMsg = document.getElementById("permissions-status-msg");
    const csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";

    const globalMode = form.querySelector('input[name="global_mode"]:checked')?.value || "full_read_only";
    const payload = {
      global_mode: globalMode,
      allow_domains_proxy: form.querySelector('input[name="allow_domains_proxy"]')?.checked || false,
      allow_dns: form.querySelector('input[name="allow_dns"]')?.checked || false,
      allow_php_sites: form.querySelector('input[name="allow_php_sites"]')?.checked || false,
      allow_container_apps: form.querySelector('input[name="allow_container_apps"]')?.checked || false,
      allow_databases: form.querySelector('input[name="allow_databases"]')?.checked || false,
      allow_files_read: form.querySelector('input[name="allow_files_read"]')?.checked || false,
      ask_on_demand: form.querySelector('input[name="ask_on_demand"]')?.checked || false,
      allowed_domains: document.getElementById("input_allowed_domains")?.value || "[]",
      allowed_app_ids: document.getElementById("input_allowed_app_ids")?.value || "[]",
      allowed_databases: document.getElementById("input_allowed_databases")?.value || "[]",
      allowed_file_targets: document.getElementById("input_allowed_file_targets")?.value || "[]",
    };

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-sm"></span> Saving...';
    }

    fetch("/plugins/ai_helper/api/permissions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(payload),
    })
      .then((res) => res.json())
      .then((data) => {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<i data-lucide="check" style="width: 14px; height: 14px;"></i> Save Permissions';
          if (typeof lucide !== "undefined") lucide.createIcons();
        }
        if (statusMsg) {
          if (data.status === "ok") {
            statusMsg.className = "alert alert--ok mt-sm";
            statusMsg.textContent = "✓ Permissions updated!";
            statusMsg.style.display = "block";
            this.updateAccessModeUI();
            setTimeout(() => {
              statusMsg.style.display = "none";
              this.closeDrawer();
            }, 600);
          } else {
            statusMsg.className = "alert alert--danger mt-sm";
            statusMsg.textContent = "Failed: " + (data.message || "Unknown error");
            statusMsg.style.display = "block";
          }
        }
      })
      .catch((err) => {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<i data-lucide="check" style="width: 14px; height: 14px;"></i> Save Permissions';
          if (typeof lucide !== "undefined") lucide.createIcons();
        }
        if (statusMsg) {
          statusMsg.className = "alert alert--danger mt-sm";
          statusMsg.textContent = "Error: " + err.message;
          statusMsg.style.display = "block";
        }
      });
  },
};
