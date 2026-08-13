import { esc, request, t, waitForOperation } from "./php-sites-api.js";

const root = document.querySelector("[data-php-site-create]");
const form = root?.querySelector("[data-create-form]");
const panels = [...root.querySelectorAll("[data-step-panel]")];
const indicators = [...root.querySelectorAll("[data-step-indicator]")];
const domainSelect = root?.querySelector("[name=domain_id]");
const versionSelect = root?.querySelector("[name=php_version]");
const error = root?.querySelector("[data-create-error]");
const warning = root?.querySelector("[data-create-warning]");
let step = 1;
let options = null;

function setMessage(element, message) { element.textContent = message || ""; element.hidden = !message; }

function selectedPreset() { return form.querySelector("[name=preset]:checked")?.value || "php"; }

function updatePreset() {
  const wordpress = selectedPreset() === "wordpress";
  root.querySelector("[data-wordpress-fields]").hidden = !wordpress;
  root.querySelectorAll(".php-choice-grid .settings-choice").forEach((item) => item.classList.toggle("settings-choice--active", item.querySelector("input").checked));
  ["wp-site-title", "wp-admin-user", "wp-admin-email", "wp-admin-password"].forEach((id) => { root.querySelector(`#${id}`).required = wordpress; });
  updateExtensionHint();
}

function updateExtensionHint() {
  const version = versionSelect.value;
  const wordpress = selectedPreset() === "wordpress";
  const info = wordpress ? options?.wordpress?.versions?.[version] : options?.database_extensions?.[version];
  const needs = info && info.ready === false && info.missing_packages?.length;
  const row = root.querySelector("[data-install-extensions]");
  row.hidden = !needs;
  row.querySelector("[data-extension-hint]").textContent = needs ? `${t("missing_packages")}: ${info.missing_packages.join(", ")}` : "";
}

function fillOptions() {
  domainSelect.innerHTML = `<option value="">${t("select_domain")}</option>`;
  (options.domains || []).forEach((domain) => { domainSelect.add(new Option(domain.name, domain.id)); });
  versionSelect.innerHTML = `<option value="">${t("select_php_version")}</option>`;
  (options.php_versions || []).forEach((item) => { versionSelect.add(new Option(item.version, item.version)); });
  domainSelect.disabled = !(options.domains || []).length;
  versionSelect.disabled = !(options.php_versions || []).length;
  if (options.php_versions?.length) versionSelect.value = options.php_versions[0].version;
  if (!options.domains?.length) setMessage(warning, t("no_available_domains"));
  if (!options.php_versions?.length) setMessage(warning, t("no_healthy_php_versions"));
  updateExtensionHint();
}

function review() {
  const values = new FormData(form);
  const domain = options.domains.find((item) => String(item.id) === values.get("domain_id"));
  const rows = [[t("preset"), selectedPreset() === "wordpress" ? t("wordpress") : t("plain_php")], [t("domain"), domain?.name || "—"], [t("php_version"), values.get("php_version")], [t("document_root"), values.get("document_root")], [t("create_database"), values.get("create_database") ? t("yes") : t("no")], [t("enable_ssl"), values.get("ssl") ? t("yes") : t("no")]];
  root.querySelector("[data-review]").innerHTML = rows.map(([label, value]) => `<div class="php-review__row"><span class="php-review__label">${esc(label)}</span><span class="php-review__value">${esc(value)}</span></div>`).join("");
}

function showStep(next) {
  step = next;
  panels.forEach((panel) => { const active = Number(panel.dataset.stepPanel) === step; panel.hidden = !active; panel.classList.toggle("is-active", active); });
  indicators.forEach((item) => item.classList.toggle("is-active", Number(item.dataset.stepIndicator) === step));
  root.querySelector("[data-create-back]").hidden = step === 1;
  root.querySelector("[data-create-next]").hidden = step === 3;
  root.querySelector("[data-create-submit]").hidden = step !== 3;
  if (step === 3) review();
}

async function submit(event) {
  event.preventDefault();
  setMessage(error, "");
  const values = new FormData(form);
  const body = { domain_id: Number(values.get("domain_id")), preset: selectedPreset(), php_version: values.get("php_version"), document_root: values.get("document_root"), create_database: values.get("create_database") === "on", ssl: values.get("ssl") === "on", include_www: values.get("include_www") === "on", install_missing_extensions: values.get("install_missing_extensions") === "on", wordpress: null };
  if (body.preset === "wordpress") body.wordpress = { site_title: values.get("wp_site_title"), admin_user: values.get("wp_admin_user"), admin_email: values.get("wp_admin_email"), admin_password: values.get("wp_admin_password") };
  const submitButton = root.querySelector("[data-create-submit]");
  submitButton.disabled = true;
  try {
    const accepted = await request("/sites", "POST", body);
    const operation = root.querySelector("[data-create-operation]");
    operation.hidden = false;
    const final = await waitForOperation(accepted, (state) => { operation.innerHTML = `<div class="php-operation__status">${esc(state.stage)} · ${esc(state.status)}</div><div class="php-operation__message">${esc(state.message)}</div>`; });
    if (final.status !== "succeeded") throw new Error(final.error || final.message || t("operation_failed"));
    window.location.assign(`/php-sites/${accepted.site_id}`);
  } catch (err) { setMessage(error, err.message); submitButton.disabled = false; }
}

async function init() {
  try { options = await request("/options"); fillOptions(); } catch (err) { setMessage(error, err.message); }
  form.addEventListener("submit", submit);
  form.addEventListener("change", (event) => { if (event.target.name === "preset") updatePreset(); if (event.target.name === "php_version") updateExtensionHint(); });
  root.querySelector("[data-create-next]").addEventListener("click", () => { if (!form.reportValidity()) return; showStep(Math.min(3, step + 1)); });
  root.querySelector("[data-create-back]").addEventListener("click", () => showStep(Math.max(1, step - 1)));
  updatePreset();
}

if (root) init();
