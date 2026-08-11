/**
 * modules/ssl.js — SSL issue page logic.
 * Syncs hidden form fields from dropdown, controls www checkbox visibility,
 * updates live preview of domains to be certified.
 */

document.addEventListener("app:init", () => {
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

  // Intercept submit, use global loader and async fetch for certbot
  if (form && submitBtn) {
    form.addEventListener("submit", async (e) => {
      const opt = select.options[select.selectedIndex];
      if (!opt || !opt.value) {
        e.preventDefault();
        return;
      }
      e.preventDefault();
      
      showGlobalLoader("Issuing Certificate... (This may take 30–60s)");
      try {
        const data = Object.fromEntries(new FormData(form).entries());
        form.querySelectorAll("input[type=checkbox]").forEach((cb) => {
          data[cb.name] = cb.checked;
        });
        
        await panel.post(form.action, data);
        window.location.href = "/ssl/?issued=" + encodeURIComponent(data.full_domain || "");
      } catch (err) {
        hideGlobalLoader();
        toast(err.message || "Failed to issue SSL certificate.", "danger");
      }
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

document.addEventListener("app:init", () => {
  document.querySelectorAll('.auto-renew-toggle').forEach(checkbox => {
    checkbox.addEventListener('change', async (e) => {
      const id = e.target.getAttribute('data-id');
      const checked = e.target.checked;
      e.target.disabled = true;
      try {
        await panel.post(`/ssl/api/${id}/auto-renew`, { auto_renew: checked });
        toast(checked ? "Auto-renew enabled" : "Auto-renew disabled", "success");
      } catch (err) {
        // Revert UI on failure
        e.target.checked = !checked;
        toast(err.message || "Failed to update auto-renew", "danger");
      } finally {
        e.target.disabled = false;
      }
    });
  });
});
