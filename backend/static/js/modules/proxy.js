/**
 * modules/proxy.js — Reverse proxy list + create form logic.
 * Modes: managed (panel domain + subdomain) vs external (full hostname).
 */

document.addEventListener("DOMContentLoaded", () => {
  initCreateForm();
  initDeleteButtons();
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
// ALERTS
// ---------------------------------------------------------------
function dismissAlerts() {
  ["alert-created", "alert-deleted", "alert-error"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) setTimeout(() => el.remove(), 6000);
  });
}
