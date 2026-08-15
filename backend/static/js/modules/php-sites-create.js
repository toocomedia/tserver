import { esc, request, t, waitForOperation } from "./php-sites-api.js";

const root = document.querySelector("[data-php-site-create]");
const form = root?.querySelector("[data-create-form]");
const domainSelect = root?.querySelector("[name=domain_id]");
const versionSelect = root?.querySelector("[name=php_version]");
const rootInput = root?.querySelector("[name=document_root]");
const errorAlert = root?.querySelector("[data-create-error]");
const warningAlert = root?.querySelector("[data-create-warning]");
const nextBtn = root?.querySelector("[data-create-next]");
const backBtn = root?.querySelector("[data-create-back]");
const submitBtn = root?.querySelector("[data-create-submit]");
let currentStep = 1;
let options = null;

function setError(msg) {
  if (!errorAlert) return;
  errorAlert.textContent = msg || "";
  errorAlert.hidden = !msg;
  if (msg) errorAlert.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function selectedPreset() {
  return form?.querySelector("[name=preset]:checked")?.value || "php";
}

function updatePreset() {
  const isWp = selectedPreset() === "wordpress";
  const isFilament = selectedPreset() === "filament";
  const isLaravel = selectedPreset() === "laravel" || isFilament;
  const wpFields = root.querySelector("[data-wordpress-fields]");
  const filamentFields = root.querySelector("[data-filament-fields]");
  const dbRow = root.querySelector("#plain-db-option-row");
  if (wpFields) wpFields.style.display = isWp ? "flex" : "none";
  if (filamentFields) filamentFields.style.display = isFilament ? "flex" : "none";
  if (dbRow) dbRow.style.display = (isWp || isLaravel) ? "none" : "flex";
  if (rootInput) {
    if (isLaravel) rootInput.value = "public";
    rootInput.readOnly = isLaravel;
  }

  root.querySelectorAll(".php-choice-grid .settings-choice").forEach((item) => {
    const radio = item.querySelector("input[type=radio]");
    item.classList.toggle("settings-choice--active", Boolean(radio?.checked));
  });
  updateExtensionHint();
}

function updateSslChoice() {
  const sslInput = root.querySelector("#php-site-ssl");
  const isChecked = Boolean(sslInput?.checked);
  const card = root.querySelector("#php-ssl-choice-grid-card") || root.querySelector("#php-ssl-choice-grid .settings-choice");
  if (card) card.classList.toggle("settings-choice--active", isChecked);
  const wwwWrap = root.querySelector("#php-ssl-choice-grid-www-wrap") || root.querySelector("#php-ssl-www-wrap");
  if (wwwWrap) wwwWrap.style.display = isChecked ? "block" : "none";
}

function updateExtensionHint() {
  const version = versionSelect?.value;
  const isWp = selectedPreset() === "wordpress";
  const isLaravel = ["laravel", "filament"].includes(selectedPreset());
  const info = isWp ? options?.wordpress?.versions?.[version] : isLaravel ? options?.laravel?.versions?.[version] : options?.database_extensions?.[version];
  const needs = info && info.ready === false && info.missing_packages?.length;
  const row = root.querySelector("[data-install-extensions]");
  if (row) {
    row.hidden = !needs;
    const hintEl = row.querySelector("[data-extension-hint]");
    if (hintEl) hintEl.textContent = needs ? `${t("missing_packages")}: ${info.missing_packages.join(", ")}` : "";
  }
}

function fillOptions() {
  domainSelect.innerHTML = `<option value="">${t("select_domain")}</option>`;
  (options.domains || []).forEach((d) => domainSelect.add(new Option(d.name, d.id)));
  versionSelect.innerHTML = `<option value="">${t("select_php_version")}</option>`;
  (options.php_versions || []).forEach((item) => versionSelect.add(new Option(item.version, item.version)));

  domainSelect.disabled = !(options.domains || []).length;
  versionSelect.disabled = !(options.php_versions || []).length;
  if (options.php_versions?.length) versionSelect.value = options.php_versions[0].version;
  if (!options.domains?.length) {
    warningAlert.textContent = t("no_available_domains");
    warningAlert.hidden = false;
  }
  updateExtensionHint();
}

function validateStep(stepNum) {
  setError("");
  if (stepNum === 1) {
    if (!domainSelect?.value) {
      setError(t("select_domain"));
      domainSelect?.focus();
      return false;
    }
    return true;
  }
  if (stepNum === 2) {
    if (!versionSelect?.value) {
      setError(t("select_php_version"));
      versionSelect?.focus();
      return false;
    }
    const rootVal = (rootInput?.value || "").trim();
    const rootRe = /^[A-Za-z0-9][A-Za-z0-9._\-/]*$/;
    if (!rootVal || !rootRe.test(rootVal) || rootVal.includes("..")) {
      setError(t("relative_document_root_desc"));
      rootInput?.focus();
      return false;
    }
    if (selectedPreset() === "wordpress") {
      const title = root.querySelector("#wp-site-title")?.value.trim();
      const user = root.querySelector("#wp-admin-user")?.value.trim();
      const email = root.querySelector("#wp-admin-email")?.value.trim();
      const pass = root.querySelector("#wp-admin-password")?.value || "";
      const confirmPass = root.querySelector("#wp-admin-password-confirm")?.value || "";
      const passErr = root.querySelector("#wp-password-error");

      if (!title) { setError(t("site_title")); root.querySelector("#wp-site-title")?.focus(); return false; }
      if (!user || !/^[A-Za-z0-9._-]{1,60}$/.test(user)) { setError(t("admin_user")); root.querySelector("#wp-admin-user")?.focus(); return false; }
      if (!email || !email.includes("@")) { setError(t("admin_email")); root.querySelector("#wp-admin-email")?.focus(); return false; }
      if (pass.length < 12) { setError(t("password_min_length")); root.querySelector("#wp-admin-password")?.focus(); return false; }
      if (pass !== confirmPass) {
        if (passErr) { passErr.textContent = t("passwords_do_not_match"); passErr.style.display = "block"; }
        setError(t("passwords_do_not_match"));
        root.querySelector("#wp-admin-password-confirm")?.focus();
        return false;
      }
      if (passErr) passErr.style.display = "none";
    }
    if (["laravel", "filament"].includes(selectedPreset()) && !options?.laravel?.composer_available) {
      setError(t("laravel_composer_required"));
      return false;
    }
    if (selectedPreset() === "filament") {
      const name = root.querySelector("#filament-admin-name")?.value.trim();
      const email = root.querySelector("#filament-admin-email")?.value.trim();
      const pass = root.querySelector("#filament-admin-password")?.value || "";
      const confirmPass = root.querySelector("#filament-admin-password-confirm")?.value || "";
      const passErr = root.querySelector("#filament-password-error");
      if (!name) { setError(t("name")); root.querySelector("#filament-admin-name")?.focus(); return false; }
      if (!email || !email.includes("@")) { setError(t("admin_email")); root.querySelector("#filament-admin-email")?.focus(); return false; }
      if (pass.length < 12) { setError(t("password_min_length")); root.querySelector("#filament-admin-password")?.focus(); return false; }
      if (pass !== confirmPass) {
        if (passErr) { passErr.textContent = t("passwords_do_not_match"); passErr.style.display = "block"; }
        setError(t("passwords_do_not_match"));
        root.querySelector("#filament-admin-password-confirm")?.focus();
        return false;
      }
      if (passErr) passErr.style.display = "none";
      if (!options?.filament?.composer_available) { setError(t("filament_composer_required")); return false; }
    }
    return true;
  }
  return true;
}

function review() {
  const values = new FormData(form);
  const domain = options?.domains?.find((item) => String(item.id) === values.get("domain_id"));
  const isWp = selectedPreset() === "wordpress";
  const isFilament = selectedPreset() === "filament";
  const isLaravel = selectedPreset() === "laravel" || isFilament;
  const presetIcon = isWp ? "devicon-wordpress-plain" : isFilament ? "devicon-filamentphp-plain" : isLaravel ? "devicon-laravel-original" : "devicon-php-plain";
  const presetText = isWp ? t("wordpress") : isFilament ? t("filament") : isLaravel ? t("laravel") : t("plain_php");
  const presetHtml = `<span style="display:inline-flex; align-items:center; gap:8px;"><i class="${presetIcon}" aria-hidden="true" style="font-size:16px; color:var(--color-accent);"></i><strong>${esc(presetText)}</strong></span>`;

  const rows = [
    [t("preset"), presetHtml, true],
    [t("domain"), domain?.name || "—"],
    [t("php_version"), values.get("php_version") || "—"],
    [t("document_root"), values.get("document_root") || "public"],
    [t("database"), (isWp || isLaravel) ? `${t("yes")} (MariaDB)` : values.get("create_database") ? t("yes") : t("no")],
    [t("enable_ssl"), values.get("ssl") ? `${t("yes")} (${values.get("include_www") ? "include www" : "single host"})` : t("no")],
  ];
  if (isWp) {
    rows.push([t("site_title"), values.get("wp_site_title") || "—"]);
    rows.push([t("admin_user"), values.get("wp_admin_user") || "—"]);
    rows.push([t("admin_email"), values.get("wp_admin_email") || "—"]);
  }
  if (isFilament) {
    rows.push([t("name"), values.get("filament_admin_name") || "—"]);
    rows.push([t("admin_email"), values.get("filament_admin_email") || "—"]);
    rows.push([t("filament_admin_url"), "/admin"]);
  }
  const reviewContainer = root.querySelector("[data-review]");
  if (reviewContainer) {
    reviewContainer.innerHTML = rows.map(([label, val, isRawHtml]) => `
      <div class="info-row-strict">
        <div class="info-row-strict__label">${esc(label)}</div>
        <div class="info-row-strict__val">${isRawHtml ? val : esc(val)}</div>
      </div>
    `).join("");
  }
}

export function goToStep(target) {
  if (target > currentStep) {
    for (let s = currentStep; s < target; s++) {
      if (!validateStep(s)) return;
    }
  }
  currentStep = target;
  for (let i = 1; i <= 3; i++) {
    const panel = root.querySelector(`#step-panel-${i}`);
    if (panel) panel.style.display = i === currentStep ? "flex" : "none";
    const nav = document.querySelector(`#nav-step-${i}`);
    if (nav) nav.classList.toggle("active", i === currentStep);
  }
  if (backBtn) backBtn.style.display = currentStep > 1 ? "inline-flex" : "none";
  if (nextBtn) nextBtn.style.display = currentStep < 3 ? "inline-flex" : "none";
  if (submitBtn) submitBtn.style.display = currentStep === 3 ? "inline-flex" : "none";
  if (currentStep === 3) review();
}
window.goToStep = goToStep;

async function handleSubmit(event) {
  event.preventDefault();
  if (!validateStep(1) || !validateStep(2)) return;
  setError("");
  const values = new FormData(form);
  const isWp = selectedPreset() === "wordpress";
  const isFilament = selectedPreset() === "filament";
  const isLaravel = selectedPreset() === "laravel" || isFilament;
  const body = {
    domain_id: Number(values.get("domain_id")),
    preset: selectedPreset(),
    php_version: values.get("php_version"),
    document_root: values.get("document_root"),
    create_database: (isWp || isLaravel) ? true : values.get("create_database") === "on",
    ssl: values.get("ssl") === "on",
    include_www: values.get("include_www") === "on",
    install_missing_extensions: values.get("install_missing_extensions") === "on",
    wordpress: isWp ? {
      site_title: values.get("wp_site_title"),
      admin_user: values.get("wp_admin_user"),
      admin_email: values.get("wp_admin_email"),
      admin_password: values.get("wp_admin_password"),
    } : null,
    filament: isFilament ? {
      admin_name: values.get("filament_admin_name"),
      admin_email: values.get("filament_admin_email"),
      admin_password: values.get("filament_admin_password"),
    } : null,
  };

  submitBtn.disabled = true;
  submitBtn.classList.add("is-loading");
  try {
    const accepted = await request("/sites", "POST", body);
    const operation = root.querySelector("[data-create-operation]");
    if (operation) operation.hidden = false;
    const final = await waitForOperation(accepted, (state) => {
      if (operation) {
        operation.innerHTML = `<div class="php-operation__status">${esc(state.stage)} · ${esc(state.status)}</div><div class="php-operation__message">${esc(state.message)}</div>`;
      }
    });
    if (final.status !== "succeeded") throw new Error(final.error || final.message || t("operation_failed"));
    window.location.assign(`/php-sites/${accepted.site_id}`);
  } catch (err) {
    setError(err.message);
    submitBtn.disabled = false;
    submitBtn.classList.remove("is-loading");
  }
}

async function init() {
  try {
    options = await request("/options");
    fillOptions();
  } catch (err) { setError(err.message); }

  form?.addEventListener("submit", handleSubmit);
  form?.addEventListener("change", (e) => {
    if (e.target.name === "preset") updatePreset();
    if (e.target.name === "ssl" || e.target.id === "php-site-ssl") updateSslChoice();
    if (e.target.name === "php_version") updateExtensionHint();
  });
  nextBtn?.addEventListener("click", () => goToStep(currentStep + 1));
  backBtn?.addEventListener("click", () => goToStep(currentStep - 1));
  updatePreset();
  updateSslChoice();
}

if (root) init();
