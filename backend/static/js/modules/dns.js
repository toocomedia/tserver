/**
 * modules/dns.js — DNS records page logic & Diagnostics Engine
 */

// Content label + placeholder per record type
const TYPE_CONFIG = {
  A:     { label: "IPv4 Address",                   placeholder: "194.62.97.174" },
  AAAA:  { label: "IPv6 Address",                   placeholder: "2001:db8::1" },
  CNAME: { label: "Target Hostname",                placeholder: "target.example.com." },
  MX:    { label: "Priority + Mail Server",         placeholder: "10 mail.example.com." },
  TXT:   { label: "Text Value",                     placeholder: "v=spf1 include:example.com ~all" },
  NS:    { label: "Nameserver Hostname",            placeholder: "ns1.example.com." },
  SRV:   { label: "Priority Weight Port Target",   placeholder: "10 20 443 target.example.com." },
  CAA:   { label: "Flag Tag Value",                 placeholder: "0 issue \"letsencrypt.org\"" },
};

/**
 * Update content label and hints
 */
function updateContentLabel(type) {
  const cfg = TYPE_CONFIG[type] || { label: "Value", placeholder: "" };
  const label = document.getElementById("content-label");
  const input = document.getElementById("rec-content");
  const hint  = document.getElementById("content-hint");

  if (label) label.textContent = cfg.label;
  if (input) input.placeholder = cfg.placeholder;

  const hints = {
    MX:  "e.g. 10 mail.example.com.",
    SRV: "e.g. 10 20 443 target.example.com.",
    CAA: "e.g. 0 issue \"letsencrypt.org\"",
    CNAME: "e.g. target.example.com.",
    NS:  "e.g. ns1.example.com.",
  };
  if (hint) hint.textContent = hints[type] || "";
}

/**
 * Automatically clean and normalize the input values directly in place
 */
window.autoCleanDnsInputs = function() {
  const typeEl = document.getElementById("rec-type");
  const nameEl = document.getElementById("rec-name");
  const contentEl = document.getElementById("rec-content");

  if (!typeEl || !nameEl || !contentEl) return;

  const rtype = typeEl.value.trim().toUpperCase();
  let rawName = nameEl.value.trim();
  let rawContent = contentEl.value.trim();
  const domain = (typeof CURRENT_DOMAIN !== "undefined") ? CURRENT_DOMAIN.toLowerCase() : "";

  // Clean Name
  if (rawName) {
    let normName = rawName.replace(/^https?:\/\//i, "").split("/")[0].split(":")[0].trim().replace(/\.+$/, "");
    if (normName) {
      if (domain && (normName.toLowerCase() === domain || normName.toLowerCase() === domain + ".")) {
        normName = "@";
      } else if (domain && normName.toLowerCase().endsWith("." + domain)) {
        normName = normName.slice(0, -(domain.length + 1)).replace(/\.+$/, "") || "@";
      }
      nameEl.value = normName;
    }
  }

  // Clean Content
  if (rawContent) {
    let normContent = rawContent;
    if (rtype === "A") {
      let clean = normContent.replace(/^https?:\/\//i, "").split("/")[0];
      if (clean.includes(":") && !clean.startsWith("[")) clean = clean.split(":")[0];
      normContent = clean.trim();
    } else if (rtype === "AAAA") {
      let clean = normContent.replace(/^https?:\/\//i, "").split("/")[0];
      if (clean.includes("[") && clean.includes("]")) {
        const m = clean.match(/\[([a-fA-F0-9:]+)\]/);
        if (m) clean = m[1];
      }
      normContent = clean.replace(/^[\[\]]+|[\[\]]+$/g, "").trim();
    } else if (rtype === "NS" || rtype === "CNAME") {
      let clean = normContent.replace(/^https?:\/\//i, "").split("/")[0].toLowerCase().trim();
      if (clean === "@" && domain) clean = domain + ".";
      if (clean && clean.includes(".") && !clean.endsWith(".")) clean = clean + ".";
      normContent = clean;
    } else if (rtype === "MX") {
      let parts = normContent.split(/\s+/);
      if (parts.length === 1 && parts[0]) {
        let host = parts[0].replace(/^https?:\/\//i, "").split("/")[0].toLowerCase().trim();
        if (host.includes(".") && !host.endsWith(".")) host += ".";
        normContent = `10 ${host}`;
      } else if (parts.length >= 2) {
        let prio = parts[0];
        let host = parts[1].replace(/^https?:\/\//i, "").split("/")[0].toLowerCase().trim();
        if (host.includes(".") && !host.endsWith(".")) host += ".";
        normContent = `${prio} ${host}`;
      }
    } else if (rtype === "CAA") {
      let parts = normContent.split(/\s+/);
      if (parts.length === 2 && isNaN(parts[0])) {
        normContent = `0 ${parts[0]} "${parts[1].replace(/"/g, '')}"`;
      }
    }
    contentEl.value = normContent;
  }
};

/**
 * Live DNS Diagnostics Runner
 */
window.runDnsDiagnostics = async function(domain) {
  const heroBanner = document.getElementById("diag-hero-banner");
  const heroTitle = document.getElementById("diag-hero-title");
  const heroDesc = document.getElementById("diag-hero-desc");
  const stepsList = document.getElementById("diag-steps-list");
  const recsBox = document.getElementById("diag-recs-box");
  const recsList = document.getElementById("diag-recs-list");
  const retestBtn = document.getElementById("btn-retest-dns");

  if (!heroBanner || !stepsList) return;

  // Reset to loading state
  heroBanner.className = "alert alert--info";
  heroTitle.textContent = "Running DNS Diagnostics...";
  heroDesc.textContent = `Testing PowerDNS, port 53, cloud firewall & public resolvers for ${domain}...`;
  stepsList.innerHTML = `<div class="text-center text-muted" style="padding:14px 0;"><div class="spinner-sm" style="margin:0 auto 8px;"></div>Running 7 verification checks...</div>`;
  if (recsBox) recsBox.classList.add("hidden");
  if (retestBtn) retestBtn.disabled = true;

  try {
    const res = await fetch(`/dns/api/${encodeURIComponent(domain)}/diagnose`, {
      headers: { "Accept": "application/json" }
    });
    const data = await res.json();

    if (retestBtn) retestBtn.disabled = false;

    // Update Hero Banner
    if (data.status === "healthy") {
      heroBanner.className = "alert alert--success";
      heroTitle.textContent = "DNS is Healthy & Resolving Globally";
    } else if (data.status === "warning") {
      heroBanner.className = "alert alert--warning";
      heroTitle.textContent = "DNS Warnings / Propagation in Progress";
    } else {
      heroBanner.className = "alert alert--danger";
      heroTitle.textContent = "Action Required: DNS Issue Detected";
    }
    heroDesc.textContent = data.summary;

    // Render Steps in clean minimal rows (no bulky cards, no window overflow)
    stepsList.innerHTML = "";
    (data.steps || []).forEach((step) => {
      const row = document.createElement("div");
      row.style.cssText = "display:flex; flex-direction:column; gap:4px; padding:10px 0; border-bottom:1px solid var(--color-line); width:100%; box-sizing:border-box;";

      const badgeClass = step.status === "pass" ? "badge--success" : (step.status === "warn" ? "badge--warning" : "badge--danger");
      const badgeLabel = step.status === "pass" ? "Pass" : (step.status === "warn" ? "Warn" : "Fail");
      const iconColor = step.status === "pass" ? "var(--color-success)" : (step.status === "warn" ? "var(--color-warning)" : "var(--color-danger)");
      const iconPath = step.status === "pass"
        ? `<path d="M20 6L9 17l-5-5" stroke-linecap="round" stroke-linejoin="round"/>`
        : step.status === "warn"
        ? `<path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>`
        : `<path d="M18 6L6 18M6 6l12 12" stroke-linecap="round" stroke-linejoin="round"/>`;

      row.innerHTML = `
        <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:8px;">
          <div style="display:flex; align-items:flex-start; gap:7px; font-weight:600; font-size:13px; min-width:0; flex:1;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${iconColor}" stroke-width="2.5" style="flex-shrink:0; margin-top:1px;">${iconPath}</svg>
            <span style="word-break:break-word; overflow-wrap:anywhere; line-height:1.4;">${step.title}</span>
          </div>
          <span class="badge ${badgeClass}" style="font-size:9px; padding:2px 6px; flex-shrink:0; margin-top:1px;">${badgeLabel}</span>
        </div>
        <div style="font-size:12px; color:var(--color-muted); margin-left:21px; word-break:break-word; overflow-wrap:anywhere; line-height:1.4;">${step.summary}</div>
      `;
      stepsList.appendChild(row);
    });

    // Render Recommendations
    if (data.recommendations && data.recommendations.length > 0 && recsBox && recsList) {
      recsList.innerHTML = "";
      data.recommendations.forEach((rec) => {
        const item = document.createElement("div");
        item.style.cssText = "font-size:12px; line-height:1.5; word-break:break-word; overflow-wrap:anywhere; display:flex; align-items:flex-start; gap:6px;";
        item.innerHTML = `<span style="flex-shrink:0; color:var(--color-danger); font-weight:700; margin-top:1px;">—</span><span>${rec}</span>`;
        recsList.appendChild(item);
      });
      recsBox.classList.remove("hidden");
    } else if (recsBox) {
      recsBox.classList.add("hidden");
    }

  } catch (err) {
    if (retestBtn) retestBtn.disabled = false;
    heroBanner.className = "alert alert--danger";
    heroTitle.textContent = "Failed to run DNS diagnostics";
    heroDesc.textContent = err.message || "Network error while reaching server.";
    stepsList.innerHTML = `<div class="text-danger text-small">Diagnostic request failed.</div>`;
  }
};

/**
 * Submit an AJAX POST for deletion and remove row live
 */
async function postDeleteRecord(btn, domain, name, type, content) {
  try {
    const payload = { name, type };
    if (content) payload.content = content;
    const csrfToken = typeof getCsrfToken === "function" ? getCsrfToken() : "";
    const res = await fetch(`/dns/${encodeURIComponent(domain)}/records/delete`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
      },
      body: new URLSearchParams({ ...payload, csrf_token: csrfToken }),
    });

    if (res.ok) {
      const row = btn.closest("tr");
      if (row) {
        row.style.transition = "opacity 0.25s ease";
        row.style.opacity = "0";
        setTimeout(() => row.remove(), 250);
      }
      if (typeof toast === "function") toast(`Deleted ${type} record "${name}".`, "success");
      if (typeof window.refreshTasks === "function") window.refreshTasks();
    } else {
      const err = await res.json().catch(() => ({}));
      if (typeof toast === "function") toast(err.error || err.detail || "Failed to delete record.", "danger");
      btn.disabled = false;
      btn.textContent = "Delete";
    }
  } catch (err) {
    if (typeof toast === "function") toast(err.message || "Network error while deleting record.", "danger");
    btn.disabled = false;
    btn.textContent = "Delete";
  }
}

function bindDeleteButtons() {
  document.querySelectorAll(".btn-del-record").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();

      const domain = btn.getAttribute("data-domain") || "";
      const name = btn.getAttribute("data-name") || "";
      const type = btn.getAttribute("data-type") || "";
      const content = btn.getAttribute("data-content") || "";

      if (!domain || !name || !type) {
        if (typeof toast === "function") toast("Missing record data for delete", "danger");
        return;
      }

      if (type.toUpperCase() === "SOA") {
        if (typeof toast === "function") toast("SOA records cannot be deleted", "danger");
        return;
      }

      const promptMsg = content ? `Delete ${type} record "${name}" → "${content}" from ${domain}?` : `Delete ${type} record "${name}" from ${domain}?`;

      confirmAction(
        promptMsg,
        async () => {
          btn.disabled = true;
          btn.textContent = "…";
          await postDeleteRecord(btn, domain, name, type, content);
        },
        { danger: true, title: "Delete Record", okLabel: "Delete Record", itemName: name }
      );
    });
  });
}

// Initialization on DOMContentLoaded / app:init
document.addEventListener("DOMContentLoaded", () => {
  const typeSelect = document.getElementById("rec-type");
  if (typeSelect) {
    updateContentLabel(typeSelect.value);
  }

  const form = document.getElementById("add-record-form");
  const saveBtn = document.getElementById("btn-save-record");
  if (form && saveBtn) {
    form.addEventListener("submit", () => {
      autoCleanDnsInputs();
      saveBtn.textContent = "Adding...";
      saveBtn.disabled = true;
    });
  }

  const templateForm = document.getElementById("template-form");
  if (templateForm) {
    templateForm.addEventListener("submit", (e) => {
      const sel = document.getElementById("template-select");
      if (!sel.value) {
        e.preventDefault();
        return;
      }
      const applyBtn = document.getElementById("btn-apply-template");
      applyBtn.textContent = "Applying...";
      applyBtn.disabled = true;
    });
  }

  bindDeleteButtons();

  // Deep-link: /dns/{domain}/records?add=1 opens modal
  const params = new URLSearchParams(window.location.search);
  if (params.get("add") === "1") {
    openModal("add-record-modal");
  } else if (params.get("diagnose") === "1" && typeof CURRENT_DOMAIN !== "undefined") {
    openModal("dns-diagnostics-modal");
    runDnsDiagnostics(CURRENT_DOMAIN);
  }

  ["alert-success", "alert-error"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) setTimeout(() => el.remove(), 5000);
  });
});


