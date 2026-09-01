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
      row.style.display = "flex";
      row.style.flexDirection = "column";
      row.style.padding = "8px 10px";
      row.style.borderBottom = "1px solid var(--color-line)";
      row.style.width = "100%";
      row.style.boxSizing = "border-box";
      row.style.overflow = "hidden";
      
      const badgeClass = step.status === "pass" ? "badge--success" : (step.status === "warn" ? "badge--warning" : "badge--danger");
      const badgeLabel = step.status === "pass" ? "Pass" : (step.status === "warn" ? "Warn" : "Fail");
      const iconSymbol = step.status === "pass" ? "✓" : (step.status === "warn" ? "!" : "✕");
      const iconColor = step.status === "pass" ? "var(--color-success)" : (step.status === "warn" ? "var(--color-warning)" : "var(--color-danger)");

      row.innerHTML = `
        <div style="display:flex; align-items:center; justify-content:space-between; gap:6px; width:100%;">
          <div style="display:flex; align-items:center; gap:6px; font-weight:600; font-size:13px; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
            <span style="color:${iconColor}; font-weight:700; flex-shrink:0;">${iconSymbol}</span>
            <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${step.title}</span>
          </div>
          <span class="badge ${badgeClass}" style="font-size:9px; padding:1px 6px; flex-shrink:0;">${badgeLabel}</span>
        </div>
        <div style="font-size:12px; color:var(--color-text-muted); margin-top:2px; word-break:break-word; overflow-wrap:anywhere; line-height:1.35;">${step.summary}</div>
      `;
      stepsList.appendChild(row);
    });

    // Render Recommendations
    if (data.recommendations && data.recommendations.length > 0 && recsBox && recsList) {
      recsList.innerHTML = "";
      data.recommendations.forEach((rec) => {
        const item = document.createElement("div");
        item.style.fontSize = "12px";
        item.style.lineHeight = "1.4";
        item.style.wordBreak = "break-word";
        item.style.overflowWrap = "anywhere";
        item.innerHTML = `👉 <strong>${rec}</strong>`;
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

