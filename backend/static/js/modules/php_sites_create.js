/**
 * JS Module for PHP Site Creation Page (create.html)
 */
import { showOperationModal, pollOperation } from "./php_sites_operations.js";

function parseErrorMessage(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => {
      if (typeof item === "string") return item;
      const field = Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
      return field ? `${field}: ${item.msg}` : (item.msg || JSON.stringify(item));
    }).join("\n");
  }
  if (detail && typeof detail === "object") {
    return detail.message || JSON.stringify(detail);
  }
  return "An unexpected error occurred.";
}

window.togglePresetFields = function (preset) {
  const wpGroup = document.getElementById("wordpress-fields-group");
  const dbOptionWrap = document.getElementById("php-db-option-wrap");
  const cardPhp = document.getElementById("card-preset-php");
  const cardWp = document.getElementById("card-preset-wp");

  if (preset === "wordpress") {
    if (wpGroup) wpGroup.style.display = "block";
    if (dbOptionWrap) dbOptionWrap.style.display = "none";
    if (cardPhp) cardPhp.classList.remove("choice-card--selected");
    if (cardWp) cardWp.classList.add("choice-card--selected");
  } else {
    if (wpGroup) wpGroup.style.display = "none";
    if (dbOptionWrap) dbOptionWrap.style.display = "block";
    if (cardPhp) cardPhp.classList.add("choice-card--selected");
    if (cardWp) cardWp.classList.remove("choice-card--selected");
  }
};

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("php-site-create-form");
  if (!form) return;

  const csrfToken = document.querySelector('input[name="csrf_token"]')?.value || "";

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const preset = form.querySelector('input[name="preset"]:checked')?.value || "php";
    const domainId = parseInt(document.getElementById("domain_id").value, 10);
    const phpVersion = document.getElementById("php_version").value;
    const docRoot = document.getElementById("document_root").value.trim();
    const createDb = document.getElementById("create_database")?.checked || false;
    const ssl = document.getElementById("ssl")?.checked || false;
    const includeWww = document.getElementById("include_www")?.checked || false;
    const installExt = document.getElementById("install_missing_extensions")?.checked || false;

    if (!domainId || isNaN(domainId)) {
      alert("Please select an available domain name.");
      return;
    }

    let wpData = null;
    if (preset === "wordpress") {
      const pwd = document.getElementById("wp_admin_password").value;
      const confirmPwd = document.getElementById("wp_confirm_password").value;
      const pwdErr = document.getElementById("wp-pwd-error");

      if (pwd.length < 12) {
        if (pwdErr) {
          pwdErr.textContent = "WordPress admin password must be at least 12 characters.";
          pwdErr.style.display = "block";
        } else {
          alert("WordPress admin password must be at least 12 characters.");
        }
        return;
      }
      if (pwd !== confirmPwd) {
        if (pwdErr) {
          pwdErr.textContent = "Passwords do not match.";
          pwdErr.style.display = "block";
        }
        return;
      }
      if (pwdErr) pwdErr.style.display = "none";

      wpData = {
        site_title: document.getElementById("wp_site_title").value.trim() || "WordPress Site",
        admin_user: document.getElementById("wp_admin_user").value.trim() || "admin",
        admin_email: document.getElementById("wp_admin_email").value.trim() || "admin@example.com",
        admin_password: pwd,
      };
    }

    const payload = {
      domain_id: domainId,
      preset: preset,
      php_version: phpVersion,
      document_root: docRoot || "public",
      create_database: preset === "wordpress" ? true : createDb,
      ssl: ssl,
      include_www: includeWww,
      install_missing_extensions: installExt,
      wordpress: wpData,
    };

    const submitBtn = document.getElementById("btn-submit-php-site");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.classList.add("is-loading");
    }

    try {
      const res = await fetch("/api/php-sites/sites", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(parseErrorMessage(errData.detail));
      }

      const result = await res.json();
      showOperationModal("Provisioning Website...");
      pollOperation(
        result.operation_id,
        () => {
          window.location.href = `/php-sites/${result.site_id}`;
        },
        () => {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove("is-loading");
          }
        }
      );
    } catch (err) {
      alert(err.message);
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.remove("is-loading");
      }
    }
  });
});
