/**
 * modules/proxy.js — Reverse proxy list + create form logic.
 * Live preview: https://app.example.com → http://1.2.3.4:9000
 * SSL toggle info, submit loading state, delete confirm.
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

  const domainSelect = document.getElementById("domain_id");
  const subdomainIn  = document.getElementById("subdomain");
  const targetIpIn   = document.getElementById("target_ip");
  const targetPortIn = document.getElementById("target_port");
  const protocolSel  = document.getElementById("protocol");
  const sslCheck     = document.getElementById("enable_ssl");
  const sslInfo      = document.getElementById("ssl-info");
  const preview      = document.getElementById("proxy-preview");
  const previewLine  = document.getElementById("preview-line");
  const hintDomain   = document.getElementById("hint-domain");
  const submitBtn    = document.getElementById("btn-submit");

  function selectedDomainName() {
    const opt = domainSelect && domainSelect.options[domainSelect.selectedIndex];
    return (opt && opt.getAttribute("data-name")) || "";
  }

  function updatePreview() {
    const domain    = selectedDomainName();
    const sub       = (subdomainIn && subdomainIn.value.trim().toLowerCase()) || "";
    const ip        = (targetIpIn && targetIpIn.value.trim()) || "";
    const port      = (targetPortIn && targetPortIn.value.trim()) || "";
    const protocol  = (protocolSel && protocolSel.value) || "http";
    const frontProt = sslCheck && sslCheck.checked ? "https" : "http";

    if (hintDomain) {
      hintDomain.textContent = domain || "domain.com";
    }

    if (sslInfo) {
      sslInfo.style.display = sslCheck && sslCheck.checked ? "block" : "none";
    }

    if (!preview || !previewLine) return;

    if (!domain || !sub) {
      preview.style.display = "none";
      return;
    }

    const front = `${frontProt}://${sub}.${domain}`;
    const back  = ip && port
      ? `${protocol}://${ip}:${port}`
      : `${protocol}://…`;

    preview.style.display = "block";
    previewLine.textContent = `${front} → ${back}`;
  }

  [domainSelect, subdomainIn, targetIpIn, targetPortIn, protocolSel, sslCheck]
    .filter(Boolean)
    .forEach((el) => {
      el.addEventListener("input", updatePreview);
      el.addEventListener("change", updatePreview);
    });

  if (form && submitBtn) {
    form.addEventListener("submit", (e) => {
      if (!domainSelect || !domainSelect.value) {
        e.preventDefault();
        return;
      }
      const ssl = sslCheck && sslCheck.checked;
      submitBtn.textContent = ssl
        ? "Creating… (SSL may take 30–60s)"
        : "Creating…";
      submitBtn.disabled = true;
    });
  }

  updatePreview();
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
        `Delete reverse proxy "${name}"? This removes DNS, Nginx config, and any linked SSL cert.`,
        async () => {
          const form = document.createElement("form");
          form.method = "POST";
          form.action = `/proxy/${id}/delete`;
          document.body.appendChild(form);
          form.submit();
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
