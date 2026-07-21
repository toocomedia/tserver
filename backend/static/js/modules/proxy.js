/**
 * modules/proxy.js — Reverse proxy list + create form logic.
 * Modes: managed (panel domain + subdomain) vs external (full hostname).
 * Also handles per-proxy cache settings panel and purge actions.
 */

document.addEventListener("DOMContentLoaded", () => {
  initCreateForm();
  initDeleteButtons();
  initCacheButtons();
  dismissAlerts();
});

// ---------------------------------------------------------------
// CREATE FORM
// ---------------------------------------------------------------
function initCreateForm() {
  const form = document.getElementById("create-proxy-form");
  if (!form) return;

  const modeManaged  = document.getElementById("mode-managed");
  const modeExternal = document.getElementById("mode-external");
  const managedFields = document.getElementById("managed-fields");
  const externalFields = document.getElementById("external-fields");
  const infoManaged = document.getElementById("info-block-managed");
  const infoExternal = document.getElementById("info-block-external");

  const domainSelect = document.getElementById("domain_id");
  const subdomainIn  = document.getElementById("subdomain");
  const hostnameIn   = document.getElementById("hostname");
  const targetIpIn   = document.getElementById("target_ip");
  const targetPortIn = document.getElementById("target_port");
  const protocolSel  = document.getElementById("protocol");
  const sslCheck     = document.getElementById("enable_ssl");
  const sslInfo      = document.getElementById("ssl-info");
  const preview      = document.getElementById("proxy-preview");
  const previewLine  = document.getElementById("preview-line");
  const hintDomain   = document.getElementById("hint-domain");
  const submitBtn    = document.getElementById("btn-submit");

  // Cache section toggle
  const cacheLabel  = document.getElementById("cache-toggle-label");
  const cacheFields = document.getElementById("cache-fields");
  const cacheIcon   = document.getElementById("cache-toggle-icon");
  if (cacheLabel && cacheFields) {
    cacheLabel.addEventListener("click", () => {
      const open = cacheFields.style.display !== "none";
      cacheFields.style.display = open ? "none" : "block";
      if (cacheIcon) cacheIcon.textContent = open ? "▶" : "▼";
    });
  }

  function isExternal() {
    return modeExternal && modeExternal.checked;
  }

  function selectedDomainName() {
    const opt = domainSelect && domainSelect.options[domainSelect.selectedIndex];
    return (opt && opt.getAttribute("data-name")) || "";
  }

  function syncModeUI() {
    const external = isExternal();
    if (managedFields) managedFields.style.display = external ? "none" : "block";
    if (externalFields) externalFields.style.display = external ? "block" : "none";
    if (infoManaged) infoManaged.style.display = external ? "none" : "block";
    if (infoExternal) infoExternal.style.display = external ? "block" : "none";

    if (domainSelect) {
      domainSelect.required = !external;
      if (external) domainSelect.removeAttribute("required");
    }
    if (subdomainIn) {
      subdomainIn.required = !external;
      if (external) subdomainIn.removeAttribute("required");
    }
    if (hostnameIn) {
      hostnameIn.required = external;
      if (!external) hostnameIn.removeAttribute("required");
    }
    updatePreview();
  }

  function updatePreview() {
    const ip        = (targetIpIn && targetIpIn.value.trim()) || "";
    const port      = (targetPortIn && targetPortIn.value.trim()) || "";
    const protocol  = (protocolSel && protocolSel.value) || "http";
    const frontProt = sslCheck && sslCheck.checked ? "https" : "http";

    if (sslInfo) {
      sslInfo.style.display = sslCheck && sslCheck.checked ? "block" : "none";
    }

    if (!preview || !previewLine) return;

    let frontHost = "";
    if (isExternal()) {
      frontHost = (hostnameIn && hostnameIn.value.trim().toLowerCase()) || "";
    } else {
      const domain = selectedDomainName();
      const sub = (subdomainIn && subdomainIn.value.trim().toLowerCase()) || "";
      if (hintDomain) hintDomain.textContent = domain || "domain.com";
      if (domain && sub) frontHost = `${sub}.${domain}`;
    }

    if (!frontHost) {
      preview.style.display = "none";
      return;
    }

    const front = `${frontProt}://${frontHost}`;
    const back  = ip && port
      ? `${protocol}://${ip}:${port}`
      : `${protocol}://…`;

    preview.style.display = "block";
    previewLine.textContent = `${front} → ${back}`;
  }

  [modeManaged, modeExternal].filter(Boolean).forEach((el) => {
    el.addEventListener("change", syncModeUI);
  });

  [domainSelect, subdomainIn, hostnameIn, targetIpIn, targetPortIn, protocolSel, sslCheck]
    .filter(Boolean)
    .forEach((el) => {
      el.addEventListener("input", updatePreview);
      el.addEventListener("change", updatePreview);
    });

  if (form && submitBtn) {
    form.addEventListener("submit", (e) => {
      if (isExternal()) {
        if (!hostnameIn || !hostnameIn.value.trim()) {
          e.preventDefault();
          return;
        }
      } else {
        if (!domainSelect || !domainSelect.value || !subdomainIn || !subdomainIn.value.trim()) {
          e.preventDefault();
          return;
        }
      }
      const ssl = sslCheck && sslCheck.checked;
      submitBtn.textContent = ssl
        ? "Creating… (SSL may take 30–60s)"
        : "Creating…";
      submitBtn.disabled = true;
    });
  }

  syncModeUI();
}

// ---------------------------------------------------------------
// DELETE
// ---------------------------------------------------------------
function initDeleteButtons() {
  document.querySelectorAll("[data-proxy-id][data-full-domain]").forEach((btn) => {
    if (btn.tagName !== "BUTTON") return;
    btn.addEventListener("click", () => {
      const id   = btn.getAttribute("data-proxy-id");
      const name = btn.getAttribute("data-full-domain");
      confirmAction(
        `Delete reverse proxy "${name}"? This removes Nginx config and any linked SSL cert` +
        (btn.getAttribute("data-dns-managed") === "0" ? "." : ", and DNS."),
        async () => {
          if (typeof window.submitPost === "function") {
            window.submitPost(`/proxy/${id}/delete`);
          } else {
            toast("Refresh the page (Ctrl+F5) and try again.", "danger");
          }
        }
      );
    });
  });
}

// ---------------------------------------------------------------
// CACHE PANEL
// ---------------------------------------------------------------
function initCacheButtons() {
  document.querySelectorAll("[id^='btn-cache-'][data-proxy-id]").forEach((btn) => {
    // Only the ⚙ Cache toggle buttons (not purge/save/close)
    if (btn.id.startsWith("btn-cache-purge-") ||
        btn.id.startsWith("btn-cache-save-") ||
        btn.id.startsWith("btn-cache-close-")) return;

    const id = btn.getAttribute("data-proxy-id");
    if (!id) return;

    const panel = document.getElementById(`cache-panel-${id}`);
    if (!panel) return;

    btn.addEventListener("click", () => {
      const isOpen = panel.style.display !== "none";
      // Close all other cache panels first
      document.querySelectorAll("[id^='cache-panel-']").forEach((p) => {
        p.style.display = "none";
      });
      panel.style.display = isOpen ? "none" : "table-row";
    });
  });

  // Save buttons
  document.querySelectorAll("[id^='btn-cache-save-']").forEach((btn) => {
    const id = btn.getAttribute("data-proxy-id");
    if (!id) return;
    btn.addEventListener("click", () => saveCacheSettings(id, btn));
  });

  // Purge buttons
  document.querySelectorAll("[id^='btn-cache-purge-']").forEach((btn) => {
    const id = btn.getAttribute("data-proxy-id");
    if (!id) return;
    btn.addEventListener("click", () => purgeCache(id, btn));
  });

  // Close buttons
  document.querySelectorAll("[id^='btn-cache-close-']").forEach((btn) => {
    const id = btn.getAttribute("data-proxy-id");
    if (!id) return;
    btn.addEventListener("click", () => {
      const panel = document.getElementById(`cache-panel-${id}`);
      if (panel) panel.style.display = "none";
    });
  });
}

async function saveCacheSettings(proxyId, btn) {
  const enabled = document.getElementById(`cache-chk-${proxyId}`)?.checked || false;
  const ttl     = parseInt(document.getElementById(`cache-ttl-${proxyId}`)?.value || "10", 10);
  const auto    = parseInt(document.getElementById(`cache-auto-${proxyId}`)?.value || "0", 10);
  const msg     = document.getElementById(`cache-status-msg-${proxyId}`);
  const badge   = document.getElementById(`cache-badge-${proxyId}`);
  const purgeBtn = document.getElementById(`btn-cache-purge-${proxyId}`);

  btn.textContent = "Saving…";
  btn.disabled = true;

  try {
    const body = new URLSearchParams({
      cache_enabled: enabled ? "true" : "false",
      cache_ttl_minutes: ttl,
      cache_auto_clear_hours: auto,
    });
    const headers = { "Content-Type": "application/x-www-form-urlencoded" };
    if (typeof window.csrfHeaders === "function") {
      Object.assign(headers, window.csrfHeaders());
    } else {
      const m = document.querySelector('meta[name="csrf-token"]');
      const t = m && m.getAttribute("content");
      if (t) headers["X-CSRF-Token"] = t;
    }
    const res = await fetch(`/proxy/${proxyId}/cache/settings`, {
      method: "POST",
      headers,
      body: body.toString(),
    });
    const data = await res.json();

    if (data.ok) {
      if (msg) msg.textContent = "✓ Saved";
      if (badge) {
        badge.className = enabled
          ? "badge badge--ok badge--dot"
          : "badge badge--neutral badge--dot";
        badge.textContent = enabled
          ? `ON${data.cache_size_mb > 0 ? " · " + data.cache_size_mb + "MB" : ""}`
          : "OFF";
      }
      if (purgeBtn) purgeBtn.disabled = !enabled;
    } else {
      if (msg) msg.textContent = "Error saving";
    }
  } catch (e) {
    if (msg) msg.textContent = "Request failed";
  } finally {
    btn.textContent = "Save";
    btn.disabled = false;
  }
}

async function purgeCache(proxyId, btn) {
  const msg   = document.getElementById(`cache-status-msg-${proxyId}`);
  const badge = document.getElementById(`cache-badge-${proxyId}`);

  btn.textContent = "Purging…";
  btn.disabled = true;

  try {
    const headers =
      typeof window.csrfHeaders === "function" ? window.csrfHeaders() : {};
    const res = await fetch(`/proxy/${proxyId}/cache/purge`, {
      method: "POST",
      headers,
    });
    const data = await res.json();

    if (data.ok) {
      if (msg) msg.textContent = data.message;
      if (badge && badge.classList.contains("badge--ok")) {
        // Update size display
        badge.textContent = "ON · 0MB";
      }
    } else {
      if (msg) msg.textContent = "Purge failed";
    }
  } catch (e) {
    if (msg) msg.textContent = "Request failed";
  } finally {
    btn.textContent = "Purge Cache";
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------
// ALERTS
// ---------------------------------------------------------------
function dismissAlerts() {
  ["alert-created", "alert-deleted", "alert-error"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) setTimeout(() => el.remove(), 6000);
  });
}
