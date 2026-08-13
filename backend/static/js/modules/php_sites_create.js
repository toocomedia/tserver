/**
 * JS Module for PHP Site Creation Page (create.html)
 */
import { showOperationModal, pollOperation, parseErrorMessage } from "./php_sites_operations.js";

function showError(msg) {
  const alertBox = document.getElementById("form-error-alert");
  const cleanMsg = parseErrorMessage(msg);
  if (alertBox) {
    alertBox.textContent = cleanMsg;
    alertBox.style.display = "block";
    alertBox.scrollIntoView({ behavior: "smooth", block: "center" });
  } else {
    alert(cleanMsg);
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

  // Password Strength & Live Match Validation
  const pwdInput = document.getElementById("wp_admin_password");
  const confirmInput = document.getElementById("wp_confirm_password");
  const lengthHint = document.getElementById("wp-pwd-length-hint");
  const matchHint = document.getElementById("wp-pwd-match-hint");

  function validateLivePasswords() {
    if (!pwdInput) return;
    const val = pwdInput.value;
    const confirmVal = confirmInput ? confirmInput.value : "";

    if (lengthHint) {
      lengthHint.textContent = `${val.length} / 12 characters minimum`;
      if (val.length >= 12) {
        lengthHint.classList.remove("text-muted", "text-danger");
        lengthHint.classList.add("text-success");
      } else {
        lengthHint.classList.remove("text-success");
        lengthHint.classList.add(val.length > 0 ? "text-danger" : "text-muted");
      }
    }

    if (matchHint && confirmInput) {
      if (!confirmVal) {
        matchHint.textContent = "";
        matchHint.className = "form-hint mt-xs text-muted";
      } else if (val === confirmVal) {
        matchHint.textContent = "✓ Passwords match";
        matchHint.className = "form-hint mt-xs text-success";
      } else {
        matchHint.textContent = "✕ Passwords do not match";
        matchHint.className = "form-hint mt-xs text-danger";
      }
    }
  }

  if (pwdInput) pwdInput.addEventListener("input", validateLivePasswords);
  if (confirmInput) confirmInput.addEventListener("input", validateLivePasswords);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideError();

    const preset = form.querySelector('input[name="preset"]:checked')?.value || "php";
    const domainVal = document.getElementById("domain_id").value;
    const domainId = parseInt(domainVal, 10);
    const phpVersion = document.getElementById("php_version")?.value || "";
    let docRoot = document.getElementById("document_root").value.trim();

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
        showError("WordPress admin password must be at least 12 characters long.");
        return;
      }

      if (pwd !== confirmPwd) {
        showError("Passwords do not match.");
        return;
      }

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
        throw new Error(parseErrorMessage(errData));
      }

      const result = await res.json();
      showOperationModal("Provisioning Website...");
      pollOperation(
        result.operation_id,
        () => {
          window.location.href = `/php-sites/${result.site_id}`;
        },
        (err) => {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove("is-loading");
          }
          showError(err);
        }
      );
    } catch (err) {
      showError(err);
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.remove("is-loading");
      }
    }
  });
});
