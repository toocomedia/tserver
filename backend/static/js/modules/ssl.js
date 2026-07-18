/**
 * modules/ssl.js — SSL issue page logic.
 * Syncs hidden form fields from dropdown, controls www checkbox visibility,
 * updates live preview of domains to be certified.
 */

document.addEventListener("DOMContentLoaded", () => {
  const select      = document.getElementById("full_domain_select");
  const hiddenDomain = document.getElementById("full_domain");
  const hiddenId     = document.getElementById("domain_id");
  const wwwGroup     = document.getElementById("www-group");
  const wwwLabel     = document.getElementById("www-label");
  const wwwCheckbox  = document.getElementById("include_www");
  const preview      = document.getElementById("cert-preview");
  const previewDomains = document.getElementById("preview-domains");
  const form         = document.getElementById("issue-form");
  const submitBtn    = document.getElementById("btn-submit");

  if (!select) return;   // Not on issue page

  function updateForm() {
    const opt = select.options[select.selectedIndex];
    if (!opt || !opt.value) {
      if (hiddenDomain) hiddenDomain.value = "";
      if (hiddenId)     hiddenId.value = "";
      if (wwwGroup)     wwwGroup.style.display = "none";
      if (preview)      preview.style.display  = "none";
      return;
    }

    const domain   = opt.value;
    const domainId = opt.getAttribute("data-domain-id") || "";
    const isProxy  = opt.text.includes("proxy →");

    if (hiddenDomain) hiddenDomain.value = domain;
    if (hiddenId)     hiddenId.value     = domainId;

    // Show www checkbox only for root domains (no subdomain prefix before first dot)
    const isRoot = !isProxy && domain.split(".").length === 2;
    if (wwwGroup) {
      wwwGroup.style.display = isRoot ? "block" : "none";
      if (!isRoot && wwwCheckbox) wwwCheckbox.checked = false;
    }
    if (wwwLabel) wwwLabel.textContent = `www.${domain}`;

    updatePreview(domain, isRoot);
  }

  function updatePreview(domain, isRoot) {
    if (!preview || !previewDomains) return;
    preview.style.display = "block";
    const includeWww = isRoot && wwwCheckbox && wwwCheckbox.checked;
    const domains = [domain];
    if (includeWww) domains.push(`www.${domain}`);
    previewDomains.textContent = domains.join(",  ");
  }

  // Events
  select.addEventListener("change", updateForm);

  if (wwwCheckbox) {
    wwwCheckbox.addEventListener("change", () => {
      const opt = select.options[select.selectedIndex];
      if (opt && opt.value) {
        const isRoot = opt.value.split(".").length === 2;
        updatePreview(opt.value, isRoot);
      }
    });
  }

  // Disable submit and show loading state during certbot (can take 30–60s)
  if (form && submitBtn) {
    form.addEventListener("submit", (e) => {
      const opt = select.options[select.selectedIndex];
      if (!opt || !opt.value) {
        e.preventDefault();
        return;
      }
      submitBtn.textContent = "Issuing… (this may take 30–60s)";
      submitBtn.disabled = true;
    });
  }

  // Auto-dismiss flash alerts
  ["alert-issued", "alert-renewed", "alert-revoked", "alert-error"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) setTimeout(() => el.remove(), 6000);
  });

  // Trigger initial sync if a value is already selected (e.g. preselect_id)
  if (select.value) updateForm();
});
