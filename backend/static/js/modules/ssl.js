/**
 * modules/ssl.js — SSL issue page logic.
 * Syncs hidden form fields from dropdown, controls www checkbox visibility,
 * updates live preview of domains to be certified.
 */

function initSslIssuePage() {
  const select              = document.getElementById("full_domain_select");
  const hiddenDomain        = document.getElementById("full_domain");
  const hiddenId            = document.getElementById("domain_id");
  const wwwGroup            = document.getElementById("www-group");
  const wwwLabel            = document.getElementById("www-label");
  const wwwCheckbox         = document.getElementById("include_www");
  const autoRenewCheckbox   = document.getElementById("auto_renew");
  const previewEmpty        = document.getElementById("preview-empty-state");
  const previewDetails      = document.getElementById("preview-details");
  const previewDomainText   = document.getElementById("preview-domain-text");
  const previewAutoRenewBadge = document.getElementById("preview-auto-renew-badge");
  const form                = document.getElementById("issue-form");
  const submitBtn           = document.getElementById("btn-submit");

  if (!select || !form) return;   // Not on issue page / drawer
  if (form._sslInitialized) return;
  form._sslInitialized = true;

  function updateForm() {
    const opt = select.options[select.selectedIndex];
    if (!opt || !opt.value) {
      if (hiddenDomain) hiddenDomain.value = "";
      if (hiddenId)     hiddenId.value = "";
      if (wwwGroup)     wwwGroup.style.display = "none";
      if (previewEmpty)   previewEmpty.style.display = "block";
      if (previewDetails) previewDetails.style.display = "none";
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
    if (previewEmpty)   previewEmpty.style.display = "none";
    if (previewDetails) previewDetails.style.display = "flex";

    const includeWww = isRoot && wwwCheckbox && wwwCheckbox.checked;
    const domains = [domain];
    if (includeWww) domains.push(`www.${domain}`);

    if (previewDomainText) previewDomainText.textContent = domains.join(",  ");

    if (previewAutoRenewBadge && autoRenewCheckbox) {
      const enabledText = previewAutoRenewBadge.getAttribute("data-enabled-text") || "Enabled";
      const disabledText = previewAutoRenewBadge.getAttribute("data-disabled-text") || "Disabled";
      if (autoRenewCheckbox.checked) {
        previewAutoRenewBadge.textContent = enabledText;
        previewAutoRenewBadge.className = "badge-minimal badge-minimal--active";
      } else {
        previewAutoRenewBadge.textContent = disabledText;
        previewAutoRenewBadge.className = "badge-minimal badge-minimal--neutral";
      }
    }
  }

  // Events
  select.addEventListener("change", updateForm);

  if (wwwCheckbox) {
    wwwCheckbox.addEventListener("change", () => {
      const opt = select.options[select.selectedIndex];
      if (opt && opt.value) {
        const isRoot = !opt.text.includes("proxy →") && opt.value.split(".").length === 2;
        updatePreview(opt.value, isRoot);
      }
    });
  }

  if (autoRenewCheckbox) {
    autoRenewCheckbox.addEventListener("change", () => {
      const opt = select.options[select.selectedIndex];
      if (opt && opt.value) {
        const isRoot = !opt.text.includes("proxy →") && opt.value.split(".").length === 2;
        updatePreview(opt.value, isRoot);
      }
    });
  }

  // Submit handler: opens Task Manager drawer for live progress
  let isSubmitting = false;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (isSubmitting) return;

    updateForm();
    const opt = select.options[select.selectedIndex];
    if (!opt || !opt.value) {
      toast("Please select a valid domain.", "warning");
      select.focus();
      return;
    }
    
    isSubmitting = true;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.classList.add("is-loading");
    }
    try {
      const data = Object.fromEntries(new FormData(form).entries());
      data.full_domain = opt.value;
      data.domain_id = opt.getAttribute("data-domain-id") || "";
      form.querySelectorAll("input[type=checkbox]").forEach((cb) => {
        data[cb.name] = cb.checked;
      });
      
      await panel.post(form.action, data);
      
      // Close drawer modal if open
      if (typeof closeModal === 'function') {
        closeModal('issue-ssl-drawer-modal');
      }

      // Open Task Drawer to show real-time Certbot progress
      if (typeof window.openTaskDrawer === 'function') {
        window.openTaskDrawer('active');
      }
      if (typeof window.refreshTasks === 'function') {
        window.refreshTasks();
      }

      if (window.toast) {
        window.toast("SSL issuance started in background.", "success");
      }

      isSubmitting = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.remove("is-loading");
      }

      // If on standalone /ssl/issue page, navigate to list; if on /ssl/ index drawer, stay live without reload!
      if (window.location.pathname.startsWith('/ssl/issue')) {
        setTimeout(() => {
          window.location.href = "/ssl/";
        }, 400);
      }
    } catch (err) {
      isSubmitting = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.remove("is-loading");
      }
      toast(err.message || "Failed to issue SSL certificate.", "danger");
    }
  });

  // Auto-dismiss flash alerts
  ["alert-issued", "alert-renewed", "alert-revoked", "alert-error"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) setTimeout(() => el.remove(), 6000);
  });

  // Initial sync immediately
  updateForm();
}

document.addEventListener("DOMContentLoaded", initSslIssuePage);
document.addEventListener("app:init", initSslIssuePage);

function initAutoRenewToggles() {
  if (document._sslAutoRenewInitialized) return;
  document._sslAutoRenewInitialized = true;

  document.addEventListener('change', async (e) => {
    if (!e.target.classList.contains('auto-renew-toggle')) return;
    const checkbox = e.target;
    const id = checkbox.getAttribute('data-id');
    const checked = checkbox.checked;
    checkbox.disabled = true;
    try {
      await panel.post(`/ssl/api/${id}/auto-renew`, { auto_renew: checked });
      toast(checked ? "Auto-renew enabled" : "Auto-renew disabled", "success");
    } catch (err) {
      checkbox.checked = !checked;
      toast(err.message || "Failed to update auto-renew", "danger");
    } finally {
      checkbox.disabled = false;
    }
  });
}

document.addEventListener("DOMContentLoaded", initAutoRenewToggles);
document.addEventListener("app:init", initAutoRenewToggles);
