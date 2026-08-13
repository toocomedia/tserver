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

function showError(msg) {
  const alertBox = document.getElementById("form-error-alert");
  if (alertBox) {
    alertBox.textContent = msg;
    alertBox.style.display = "block";
    alertBox.scrollIntoView({ behavior: "smooth", block: "center" });
  } else {
    alert(msg);
  }
}

function hideError() {
  const alertBox = document.getElementById("form-error-alert");
  if (alertBox) alertBox.style.display = "none";
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

  // Domain SSL detector
  const domainSelect = document.getElementById("domain_id");
  const sslNote = document.getElementById("domain-ssl-note");
  const sslCheckbox = document.getElementById("ssl");

  if (domainSelect) {
    domainSelect.addEventListener("change", () => {
      const selectedOpt = domainSelect.options[domainSelect.selectedIndex];
      const hasSsl = selectedOpt ? selectedOpt.dataset.hasSsl === "1" : false;
      if (hasSsl) {
        if (sslNote) sslNote.style.display = "block";
        if (sslCheckbox) sslCheckbox.checked = false;
      } else {
        if (sslNote) sslNote.style.display = "none";
        if (sslCheckbox) sslCheckbox.checked = true;
      }
    });
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideError();

    const preset = form.querySelector('input[name="preset"]:checked')?.value || "php";
    const domainVal = document.getElementById("domain_id").value;
    const domainId = parseInt(domainVal, 10);
    const phpVersion = document.getElementById("php_version")?.value || "";
    let docRoot = document.getElementById("document_root").value.trim();

    // Sanitize document root (strip leading/trailing slashes)
    docRoot = docRoot.replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+$/, "");
    if (!docRoot) docRoot = "public";

    const createDb = document.getElementById("create_database")?.checked || false;
    const ssl = document.getElementById("ssl")?.checked || false;
    const includeWww = document.getElementById("include_www")?.checked || false;
    const installExt = document.getElementById("install_missing_extensions")?.checked || false;

    if (!domainVal || isNaN(domainId) || domainId <= 0) {
      showError("Please select an available domain name from the dropdown.");
      return;
    }

    if (!phpVersion) {
      showError("Please select an installed PHP version.");
      return;
    }

    let wpData = null;
    if (preset === "wordpress") {
      const title = document.getElementById("wp_site_title").value.trim() || "WordPress Site";
      const user = document.getElementById("wp_admin_user").value.trim() || "admin";
      const email = document.getElementById("wp_admin_email").value.trim();
      const pwd = document.getElementById("wp_admin_password").value;
      const confirmPwd = document.getElementById("wp_confirm_password").value;
      const pwdErr = document.getElementById("wp-pwd-error");

      if (!email || !email.includes("@")) {
        showError("Please enter a valid WordPress administrator email.");
        return;
      }

      const wpUserRegex = /^[A-Za-z0-9._-]{1,60}$/;
      if (!wpUserRegex.test(user)) {
        showError("WordPress username contains invalid characters. Use letters, numbers, dots, hyphens, or underscores.");
        return;
      }

      if (pwd.length < 12) {
        if (pwdErr) {
          pwdErr.textContent = "WordPress admin password must be at least 12 characters.";
          pwdErr.style.display = "block";
        }
        showError("WordPress admin password must be at least 12 characters.");
        return;
      }

      if (pwd !== confirmPwd) {
        if (pwdErr) {
          pwdErr.textContent = "Passwords do not match.";
          pwdErr.style.display = "block";
        }
        showError("Passwords do not match.");
        return;
      }
      if (pwdErr) pwdErr.style.display = "none";

      wpData = {
        site_title: title,
        admin_user: user,
        admin_email: email,
        admin_password: pwd,
      };
    }

    const payload = {
      domain_id: domainId,
      preset: preset,
      php_version: phpVersion,
      document_root: docRoot,
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
      showError(err.message);
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.remove("is-loading");
      }
    }
  });
});
