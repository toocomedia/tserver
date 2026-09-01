/**
 * modules/dns.js — DNS records page logic & Diagnostics Engine
 * Handles: dynamic content label, auto-correction, split drawers, live diagnostics
 */

// Content label + placeholder per record type
const TYPE_CONFIG = {
  A:     { label: "IPv4 Address",                   placeholder: "194.62.97.174" },
  AAAA:  { label: "IPv6 Address",                   placeholder: "2001:db8::1" },
  CNAME: { label: "Target Hostname",                placeholder: "example.com." },
  MX:    { label: "Priority + Mail Server",         placeholder: "10 mail.example.com." },
  TXT:   { label: "Text Value",                     placeholder: "v=spf1 include:example.com ~all" },
  NS:    { label: "Nameserver Hostname",            placeholder: "ns1.example.com." },
  SRV:   { label: "Priority Weight Port Target",   placeholder: "10 20 443 target.example.com." },
  CAA:   { label: "Flag Tag Value",                 placeholder: "0 issue \"letsencrypt.org\"" },
};

/**
 * Drawer Toggle Functions
 */
window.openDnsDrawer = function(drawerId) {
  const backdrop = document.getElementById(`${drawerId}-backdrop`);
  if (backdrop) {
    backdrop.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }
};

window.closeDnsDrawer = function(drawerId) {
  const backdrop = document.getElementById(`${drawerId}-backdrop`);
  if (backdrop) {
    backdrop.classList.add("hidden");
    document.body.style.overflow = "";
  }
};

// Close on backdrop click or ESC key
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeDnsDrawer("add-record-drawer");
    closeDnsDrawer("dns-diagnostics-drawer");
  }
});

document.addEventListener("click", (e) => {
  if (e.target.classList && e.target.classList.contains("dns-drawer-backdrop")) {
    e.target.classList.add("hidden");
    document.body.style.overflow = "";
  }
});

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
    A:   "Auto-cleans URLs (http://1.2.3.4:80/path) and CIDR (/32) to plain IP.",
    AAAA:"Auto-cleans brackets [2001:db8::1] and ports.",
    MX:  "Auto-defaults priority to 10 and appends trailing dot if omitted.",
    SRV: "Format: <priority> <weight> <port> <target>",
    CAA: "Auto-formats flag and quotes: e.g. 0 issue \"letsencrypt.org\"",
    CNAME: "Auto-appends trailing dot for hostnames: target.com.",
    NS:  "Auto-appends trailing dot: ns1.example.com.",
    TXT: "Auto-escapes and wraps quotes safely for SPF/DKIM.",
  };
  if (hint) hint.textContent = hints[type] || "";
}

/**
 * Live Auto-Correction Preview
 */
window.updateAutoCorrectionPreview = function() {
  const typeEl = document.getElementById("rec-type");
  const nameEl = document.getElementById("rec-name");
  const contentEl = document.getElementById("rec-content");
  const previewBox = document.getElementById("correction-preview-box");
  const previewText = document.getElementById("correction-preview-text");

  if (!typeEl || !nameEl || !contentEl || !previewBox || !previewText) return;

  const rtype = typeEl.value.trim().toUpperCase();
  const rawName = nameEl.value.trim();
  const rawContent = contentEl.value.trim();

  if (!rawContent && !rawName) {
    previewBox.classList.add("hidden");
    return;
  }

  let normName = rawName || "@";
  normName = normName.replace(/^https?:\/\//i, "").split("/")[0].split(":")[0];
  const domain = (typeof CURRENT_DOMAIN !== "undefined") ? CURRENT_DOMAIN.toLowerCase() : "";
  if (domain && (normName.toLowerCase() === domain || normName.toLowerCase() === domain + ".")) {
    normName = "@";
  } else if (domain && normName.toLowerCase().endsWith("." + domain)) {
    normName = normName.slice(0, -(domain.length + 1)) || "@";
  }

  let normContent = rawContent;
  let wasCorrected = false;

  if (rtype === "A") {
    let clean = normContent.replace(/^https?:\/\//i, "").split("/")[0];
    if (clean.includes(":") && !clean.startsWith("[")) clean = clean.split(":")[0];
    if (clean !== rawContent) { normContent = clean; wasCorrected = true; }
  } else if (rtype === "AAAA") {
    let clean = normContent.replace(/^https?:\/\//i, "").split("/")[0].replace(/^\[|\]$/g, "");
    if (clean !== rawContent) { normContent = clean; wasCorrected = true; }
  } else if (rtype === "NS" || rtype === "CNAME") {
    let clean = normContent.replace(/^https?:\/\//i, "").split("/")[0].toLowerCase();
    if (clean === "@" && domain) clean = domain + ".";
    if (clean.includes(".") && !clean.endsWith(".")) clean = clean + ".";
    if (clean !== rawContent) { normContent = clean; wasCorrected = true; }
  } else if (rtype === "MX") {
    let parts = normContent.split(/\s+/);
    if (parts.length === 1 && parts[0]) {
      let host = parts[0].replace(/^https?:\/\//i, "").split("/")[0].toLowerCase();
      if (host.includes(".") && !host.endsWith(".")) host += ".";
      normContent = `10 ${host}`;
      wasCorrected = true;
    } else if (parts.length >= 2) {
      let prio = parts[0];
      let host = parts[1].replace(/^https?:\/\//i, "").split("/")[0].toLowerCase();
      if (host.includes(".") && !host.endsWith(".")) host += ".";
      normContent = `${prio} ${host}`;
      if (normContent !== rawContent) wasCorrected = true;
    }
  } else if (rtype === "CAA") {
    let parts = normContent.split(/\s+/);
    if (parts.length === 2 && isNaN(parts[0])) {
      normContent = `0 ${parts[0]} "${parts[1].replace(/"/g, '')}"`;
      wasCorrected = true;
    }
  }

  if (rawName !== normName) wasCorrected = true;

  if (wasCorrected && (rawName || rawContent)) {
    previewText.innerHTML = `Name: <code>${normName}</code> &nbsp;|&nbsp; Content: <code>${normContent}</code>`;
    previewBox.classList.remove("hidden");
  } else {
    previewBox.classList.add("hidden");
  }
};

/**
 * Live DNS Diagnostics Runner
 */
window.runDnsDiagnostics = async function(domain) {
  const heroBanner = document.getElementById("diag-hero-banner");
  const heroTitle = document.getElementById("diag-hero-title");
  const heroDesc = document.getElementById("diag-hero-desc");
  const heroIcon = document.getElementById("diag-hero-icon");
  const stepsList = document.getElementById("diag-steps-list");
  const recsBox = document.getElementById("diag-recs-box");
  const recsList = document.getElementById("diag-recs-list");
  const retestBtn = document.getElementById("btn-retest-dns");

  if (!heroBanner || !stepsList) return;

  // Reset to loading state
  heroBanner.className = "dns-diag-hero dns-diag-hero--loading";
  heroIcon.innerHTML = `<div class="spinner-sm"></div>`;
  heroTitle.textContent = "Running Full DNS Diagnostics...";
  heroDesc.textContent = `Inspecting PowerDNS, local port 53, firewall, glue records & resolvers for ${domain}...`;
  stepsList.innerHTML = `<div class="text-center text-muted" style="padding:20px 0;"><div class="spinner-sm" style="margin:0 auto 10px;"></div>Running 7 verification checks...</div>`;
  if (recsBox) recsBox.classList.add("hidden");
  if (retestBtn) retestBtn.disabled = true;

  try {
    const res = await fetch(`/dns/api/${encodeURIComponent(domain)}/diagnose`, {
      headers: { "Accept": "application/json" }
    });
    const data = await res.json();

    if (retestBtn) retestBtn.disabled = false;

    // Update Hero Banner
    heroBanner.className = `dns-diag-hero dns-diag-hero--${data.status}`;
    if (data.status === "healthy") {
      heroIcon.innerHTML = `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#10b981" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
      heroTitle.textContent = "DNS is Healthy & Resolving Globally";
    } else if (data.status === "warning") {
      heroIcon.innerHTML = `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#f59e0b" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;
      heroTitle.textContent = "DNS Warnings / Propagation in Progress";
    } else {
      heroIcon.innerHTML = `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#ef4444" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>`;
      heroTitle.textContent = "Action Required: DNS Issues Detected";
    }
    heroDesc.textContent = data.summary;

    // Render Steps
    stepsList.innerHTML = "";
    (data.steps || []).forEach((step) => {
      const card = document.createElement("div");
      card.className = "dns-step-card";
      
      const badgeClass = `dns-step-badge--${step.status}`;
      const badgeLabel = step.status === "pass" ? "Passed" : (step.status === "warn" ? "Warning" : "Failed");
      const iconColor = step.status === "pass" ? "#10b981" : (step.status === "warn" ? "#f59e0b" : "#ef4444");

      card.innerHTML = `
        <div class="dns-step-header">
          <div class="dns-step-title">
            <span style="color:${iconColor}; font-weight:700;">${step.status === 'pass' ? '✓' : (step.status === 'warn' ? '!' : '✕')}</span>
            <span>${step.title}</span>
          </div>
          <span class="dns-step-badge ${badgeClass}">${badgeLabel}</span>
        </div>
        <div class="dns-step-summary">${step.summary}</div>
        ${step.details ? `<div class="dns-step-details">${step.details}</div>` : ''}
      `;
      stepsList.appendChild(card);
    });

    // Render Recommendations
    if (data.recommendations && data.recommendations.length > 0 && recsBox && recsList) {
      recsList.innerHTML = "";
      data.recommendations.forEach((rec) => {
        const item = document.createElement("div");
        item.className = "dns-recommendation-item";
        item.innerHTML = `<span>👉</span> <strong>${rec}</strong>`;
        recsList.appendChild(item);
      });
      recsBox.classList.remove("hidden");
    } else if (recsBox) {
      recsBox.classList.add("hidden");
    }

  } catch (err) {
    if (retestBtn) retestBtn.disabled = false;
    heroBanner.className = "dns-diag-hero dns-diag-hero--error";
    heroIcon.innerHTML = `✕`;
    heroTitle.textContent = "Failed to run DNS diagnostics";
    heroDesc.textContent = err.message || "An unexpected error occurred while communicating with the server.";
    stepsList.innerHTML = `<div class="text-danger text-small">Diagnostic request failed. Please check network connection.</div>`;
  }
};

/**
 * Submit a real HTML form POST for deletion
 */
function postDeleteRecord(domain, name, type, content) {
  if (typeof window.submitPost !== "function") {
    if (typeof toast === "function") toast("Page scripts incomplete — hard-refresh (Ctrl+F5).", "danger");
    return;
  }
  const payload = { name, type };
  if (content) payload.content = content;
  window.submitPost(`/dns/${encodeURIComponent(domain)}/records/delete`, payload);
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
          postDeleteRecord(domain, name, type, content);
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

  // Deep-link: /dns/{domain}/records?add=1 opens drawer
  const params = new URLSearchParams(window.location.search);
  if (params.get("add") === "1") {
    openDnsDrawer("add-record-drawer");
  } else if (params.get("diagnose") === "1" && typeof CURRENT_DOMAIN !== "undefined") {
    openDnsDrawer("dns-diagnostics-drawer");
    runDnsDiagnostics(CURRENT_DOMAIN);
  }

  ["alert-success", "alert-error"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) setTimeout(() => el.remove(), 5000);
  });
});

/**
 * Convert standalone subdomain zone to parent record
 */
window.convertSubdomainToRecord = function(subdomain, parentDomain) {
  if (typeof confirmAction !== "function") {
    if (!confirm(`Convert '${subdomain}' from a standalone DNS zone into an A record inside '${parentDomain}'?`)) return;
    performConvertSubdomain(subdomain, parentDomain);
    return;
  }

  confirmAction(
    `Convert '${subdomain}' from a standalone DNS zone into an A record inside '${parentDomain}'? The separate zone will be deleted.`,
    () => performConvertSubdomain(subdomain, parentDomain),
    { danger: true, title: "Convert Subdomain", okLabel: "Convert", itemName: subdomain }
  );
};

async function performConvertSubdomain(subdomain, parentDomain) {
  if (typeof showGlobalLoader === "function") {
    showGlobalLoader("Converting DNS Zone...");
  }
  try {
    const res = await panel.post(`/dns/api/${encodeURIComponent(subdomain)}/convert-to-record`);
    if (typeof hideGlobalLoader === "function") hideGlobalLoader();
    if (typeof toast === "function") {
      toast(`Converted '${subdomain}' to an A record in '${parentDomain}'`, "success");
    }
    if (res && res.redirect_url) {
      window.location.href = res.redirect_url;
    } else {
      window.location.reload();
    }
  } catch (err) {
    if (typeof hideGlobalLoader === "function") hideGlobalLoader();
    if (typeof toast === "function") {
      toast(err.message || "Failed to convert DNS zone.", "danger");
    } else {
      alert(err.message || "Failed to convert DNS zone.");
    }
  }
}

