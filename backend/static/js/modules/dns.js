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
    NS:  "Must end with a dot: ns1.example.com.",
  };
  if (hint) hint.textContent = hints[type] || "";
}

// Apply label on page load for the default selected type
document.addEventListener("DOMContentLoaded", () => {
  const typeSelect = document.getElementById("rec-type");
  if (typeSelect) {
    updateContentLabel(typeSelect.value);
  }

  // Disable submit while form submits (prevent double click)
  const form = document.getElementById("add-record-form");
  const saveBtn = document.getElementById("btn-save-record");
  if (form && saveBtn) {
    form.addEventListener("submit", () => {
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

  // Auto-dismiss success/error alerts after 5 seconds
  ["alert-success", "alert-error"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) setTimeout(() => el.remove(), 5000);
  });
});
