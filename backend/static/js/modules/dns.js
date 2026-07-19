/**
 * modules/dns.js — DNS records page logic
 * Handles: dynamic content label, placeholder hints, form state
 */

// Content label + placeholder per record type
const TYPE_CONFIG = {
  A:     { label: "IPv4 Address",                   placeholder: "1.2.3.4" },
  AAAA:  { label: "IPv6 Address",                   placeholder: "2001:db8::1" },
  CNAME: { label: "Target Hostname",                placeholder: "example.com." },
  MX:    { label: "Priority + Mail Server",         placeholder: "10 mail.example.com." },
  TXT:   { label: "Text Value",                     placeholder: "v=spf1 include:example.com ~all" },
  NS:    { label: "Nameserver Hostname",            placeholder: "ns1.example.com." },
  SRV:   { label: "Priority Weight Port Target",   placeholder: "10 20 443 target.example.com." },
  CAA:   { label: "Flag Tag Value",                 placeholder: "0 issue \"letsencrypt.org\"" },
};

// Extra hints (quotes optional for TXT — panel auto-quotes for PowerDNS)


/**
 * updateContentLabel — called when record type dropdown changes.
 * Updates the content field label and placeholder to match the selected type.
 */
function updateContentLabel(type) {
  const cfg = TYPE_CONFIG[type] || { label: "Value", placeholder: "" };
  const label = document.getElementById("content-label");
  const input = document.getElementById("rec-content");
  const hint  = document.getElementById("content-hint");

  if (label) label.textContent = cfg.label;
  if (input) input.placeholder = cfg.placeholder;

  // Extra hints for types that need them
  const hints = {
    MX:  "Format: <priority> <hostname>  e.g. 10 mail.example.com.",
    SRV: "Format: <priority> <weight> <port> <target>",
    CAA: "Format: <flag> issue|issuewild|iodef \"<value>\"",
    CNAME: "Must end with a dot for absolute names: example.com.",
    NS:  "Must end with a dot: ns1.example.com. (REPLACE overwrites same name+type — use Child NS template for ns1+ns2.)",
    TXT: "Quotes optional — panel wraps TXT for PowerDNS automatically.",
  };
  if (hint) hint.textContent = hints[type] || "";
}

/**
 * Submit a real HTML form POST (session cookie + Form fields + CSRF).
 */
function postDeleteRecord(domain, name, type) {
  if (typeof window.submitPost !== "function") {
    toast("Page scripts incomplete — hard-refresh (Ctrl+F5).", "danger");
    return;
  }
  window.submitPost(`/dns/${encodeURIComponent(domain)}/records/delete`, {
    name,
    type,
  });
}

function bindDeleteButtons() {
  document.querySelectorAll(".btn-del-record").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();

      const domain = btn.getAttribute("data-domain") || "";
      const name = btn.getAttribute("data-name") || "";
      const type = btn.getAttribute("data-type") || "";

      if (!domain || !name || !type) {
        toast("Missing record data for delete", "danger");
        return;
      }

      if (type.toUpperCase() === "SOA") {
        toast("SOA records cannot be deleted", "danger");
        return;
      }

      confirmAction(
        `Delete ${type} record "${name}" from ${domain}?`,
        async () => {
          btn.disabled = true;
          btn.textContent = "…";
          postDeleteRecord(domain, name, type);
        }
      );
    });
  });
}

// Apply label on page load for the default selected type
document.addEventListener("DOMContentLoaded", () => {
  const typeSelect = document.getElementById("rec-type");
  if (typeSelect) {
    updateContentLabel(typeSelect.value);
  }

  // Refresh CSRF on all forms (cookie/meta may update after load)
  if (typeof window.injectCsrfIntoForms === "function") {
    window.injectCsrfIntoForms(document);
  }

  // Disable submit while form submits (prevent double click)
  const form = document.getElementById("add-record-form");
  const saveBtn = document.getElementById("btn-save-record");
  if (form && saveBtn) {
    form.addEventListener("submit", () => {
      if (typeof window.injectCsrfIntoForms === "function") {
        window.injectCsrfIntoForms(form);
      }
      saveBtn.textContent = "Adding...";
      saveBtn.disabled = true;
    });
  }

  // Confirm before template apply
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

  // Deep-link: /dns/{domain}/records?add=1 opens Add Record modal
  const params = new URLSearchParams(window.location.search);
  if (params.get("add") === "1" && typeof openModal === "function") {
    openModal("add-record-modal");
  }

  // Auto-dismiss success/error alerts after 5 seconds
  ["alert-success", "alert-error"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) setTimeout(() => el.remove(), 5000);
  });
});
