import { actionUrl, esc, request, statusTone, t, waitForOperation } from "./php-sites-api.js";

const root = document.querySelector("[data-php-site-detail]");
const siteId = root?.dataset.siteId;
const loading = root?.querySelector("[data-detail-loading]");
const content = root?.querySelector("[data-detail-content]");
const error = root?.querySelector("[data-detail-error]");
const deleteModal = root?.querySelector("[data-php-site-delete-modal]");
const deleteModalTitle = root?.querySelector("[data-php-site-delete-title]");
const deleteModalDescription = root?.querySelector("[data-php-site-delete-description]");
const deleteConfirm = root?.querySelector("[data-php-site-delete-confirm]");
const deleteConfirmationText = root?.querySelector("[data-php-site-delete-confirmation]");
const deleteDatabaseChoice = root?.querySelector("[data-php-site-delete-database]");
const deleteModalError = root?.querySelector("[data-php-site-delete-error]");
const deleteSubmit = root?.querySelector("[data-php-site-delete-submit]");
let site;
let options;
let pendingDestructiveAction;

function setError(message) { error.textContent = message || ""; error.hidden = !message; }
function badge(value) { return `<span class="status-badge status-badge--${statusTone(value)}">${esc(value || "—")}</span>`; }
function can(action) { return Boolean(site.available_actions?.[action]); }
function button(action, label, tone = "secondary", extra = "") { return `<button class="btn btn--${tone} btn--sm" type="button" data-action="${action}" ${extra}>${esc(label)}</button>`; }
function row(label, value) { return `<div class="php-detail__row"><span class="php-detail__label">${esc(label)}</span><span class="php-detail__value">${value}</span></div>`; }

function healthSection() {
  const health = site.health || {};
  const http = health.http?.status_code ? `${health.http.status_code}` : t("not_checked");
  const errors = (health.errors || []).map((item) => `<div class="form-error">${esc(item)}</div>`).join("");
  return `<section class="php-detail__section"><h2>${t("health")}</h2><div class="php-detail__rows">${row(t("overall"), badge(health.state || site.status))}${row(t("php_fpm_socket"), badge(health.socket_healthy ? "active" : "inactive"))}${row(t("nginx"), badge(health.nginx_active ? "active" : "inactive"))}${row(t("local_http"), esc(http))}${site.database ? row(t("mariadb"), badge(health.mariadb_healthy ? "active" : "inactive")) : ""}</div>${errors ? `<div class="php-detail__alert">${errors}</div>` : ""}</section>`;
}

function runtimeSection() {
  const versions = (options?.php_versions || []).map((item) => `<option value="${esc(item.version)}" ${item.version === site.php_version ? "selected" : ""}>${esc(item.version)}</option>`).join("");
  return `<section class="php-detail__section"><h2>${t("runtime_settings")}</h2><div class="php-detail__rows">${row(t("php_version"), `<div class="php-detail__inline"><select class="form-select" data-runtime-version>${versions}</select>${can("change_php_version") ? button("runtime-submit", t("change"), "secondary") : ""}</div>`)}${row(t("document_root"), `<div class="php-detail__inline"><input class="form-input" data-document-root value="${esc(site.document_root)}" pattern="[A-Za-z0-9][A-Za-z0-9._/-]*">${can("change_document_root") ? button("root-submit", t("change"), "secondary") : ""}</div>`)}</div></section>`;
}

function databaseSection() {
  if (!site.database) return `<section class="php-detail__section"><h2>${t("database")}</h2><p class="form-hint">${t("no_database_attached")}</p>${can("create_database") ? `<label class="form-check"><input type="checkbox" data-db-install><span>${t("install_missing_extensions")}</span></label>${button("db-create", t("create_database"), "primary")}` : ""}</section>`;
  const db = site.database;
  return `<section class="php-detail__section"><h2>${t("database")}</h2><div class="php-detail__rows">${row(t("database_name"), esc(db.database))}${row(t("database_user"), esc(db.username))}${row(t("host"), `${esc(db.host)}:${esc(db.port)}`)}${row(t("status"), badge(db.status))}</div><div class="php-detail__controls">${button("db-reveal", t("reveal_credentials"))}${button("db-rotate", t("rotate_password"))}${can("delete_database") ? button("db-delete", t("delete_database"), "danger") : ""}</div><div class="php-detail__credentials" data-credentials hidden></div></section>`;
}

function sslSection() {
  const ssl = site.ssl || {};
  const controls = can("issue_ssl") ? `${button("ssl-issue", t("issue_ssl"), "primary")}` : `${can("renew_ssl") ? button("ssl-renew", t("renew")) : ""}${can("revoke_ssl") ? button("ssl-revoke", t("revoke"), "danger") : ""}`;
  return `<section class="php-detail__section"><h2>${t("ssl")}</h2><div class="php-detail__rows">${row(t("status"), badge(ssl.active ? "active" : "inactive"))}${row(t("expires"), esc(ssl.expiry_date || t("not_available")))}</div><label class="form-check php-detail__ssl-www"><input type="checkbox" data-ssl-www ${ssl.include_www ? "checked" : ""}><span>${t("include_www")}</span></label><div class="php-detail__controls">${controls}</div></section>`;
}

function wordpressSection() {
  if (site.preset !== "wordpress") return "";
  const wp = site.wordpress || {};
  return `<section class="php-detail__section"><h2>${t("wordpress_settings")}</h2><div class="php-detail__rows">${row(t("site_title"), esc(wp.site_title))}${row(t("admin_user"), esc(wp.admin_user))}${row(t("admin_email"), esc(wp.admin_email))}${row(t("status"), badge(wp.installed ? "active" : "failed"))}</div>${can("wordpress_retry") ? `<div class="php-detail__controls"><input class="form-input" data-wp-password type="password" minlength="12" placeholder="${t("new_admin_password")}" autocomplete="new-password"><label class="form-check"><input type="checkbox" data-wp-install><span>${t("install_missing_extensions")}</span></label>${button("wp-retry", t("retry"), "primary")}</div>` : ""}</section>`;
}

function render() {
  let actionButtons = can("enable") ? button("control", t("enable"), "primary", 'data-value="enable"') : can("disable") ? button("control", t("disable"), "secondary", 'data-value="disable"') : "";
  if (can("repair")) actionButtons += button("repair", t("repair"), "primary");
  content.innerHTML = `<div class="php-detail__hero"><div class="php-detail__identity"><h1 class="php-detail__title">${esc(site.domain)} ${badge(site.status)}</h1><div class="php-detail__meta"><span>${esc(site.preset === "wordpress" ? t("wordpress") : t("plain_php"))}</span><span>PHP ${esc(site.php_version)}</span><span>${t("document_root")}: ${esc(site.document_root)}</span></div></div><div class="php-detail__actions">${actionButtons}</div></div>${site.last_error ? `<div class="alert alert--danger">${esc(site.last_error)}</div>` : ""}${site.last_warning ? `<div class="alert alert--warning">${esc(site.last_warning)}</div>` : ""}${site.operation ? `<div class="php-operation" data-operation-status><div class="php-operation__status">${esc(site.operation.stage)} · ${esc(site.operation.status)}</div><div class="php-operation__message">${esc(site.operation.message)}</div></div>` : ""}<div class="php-detail__grid">${healthSection()}${runtimeSection()}${databaseSection()}${sslSection()}${wordpressSection()}<section class="php-detail__section php-detail__section--wide php-detail__danger"><h2>${t("danger_zone")}</h2><p class="form-hint">${t("delete_php_site_desc")}</p>${can("delete_site") ? button("delete-site", t("delete_website"), "danger") : ""}</section></div>`;
}

function closeDeleteModal() {
  deleteModal?.classList.add("hidden");
  if (deleteConfirm) deleteConfirm.value = "";
  if (deleteModalError) { deleteModalError.textContent = ""; deleteModalError.hidden = true; }
  if (deleteSubmit) deleteSubmit.disabled = true;
  pendingDestructiveAction = null;
}

function openDestructiveModal(kind) {
  if (!site || !deleteModal) return;
  const database = site.database?.database;
  const actions = {
    site: { title: t("delete_website"), description: `${t("delete_php_site_desc")} ${site.domain}`, expected: `DELETE ${site.domain}` },
    database: { title: t("delete_database"), description: `${t("delete_database_confirmation_desc")} ${database}`, expected: `DELETE DATABASE ${database}` },
    ssl: { title: t("revoke"), description: `${t("revoke_ssl_confirmation_desc")} ${site.domain}`, expected: `REVOKE ${site.domain}` },
  };
  pendingDestructiveAction = { kind, ...actions[kind] };
  if (deleteModalTitle) deleteModalTitle.textContent = pendingDestructiveAction.title;
  if (deleteModalDescription) deleteModalDescription.textContent = pendingDestructiveAction.description;
  if (deleteConfirmationText) deleteConfirmationText.textContent = pendingDestructiveAction.expected;
  if (deleteDatabaseChoice) deleteDatabaseChoice.hidden = kind !== "site" || !site.database;
  deleteDatabaseChoice?.querySelector('input[value="false"]')?.click();
  if (deleteSubmit) deleteSubmit.textContent = pendingDestructiveAction.title;
  deleteModal.classList.remove("hidden");
  window.setTimeout(() => deleteConfirm?.focus(), 0);
}

function validateDeleteConfirmation() {
  if (!pendingDestructiveAction || !deleteConfirm || !deleteSubmit) return false;
  const valid = deleteConfirm.value.trim() === pendingDestructiveAction.expected;
  deleteSubmit.disabled = !valid;
  if (deleteModalError && valid) { deleteModalError.textContent = ""; deleteModalError.hidden = true; }
  return valid;
}

function showCredentials(data) { const target = content.querySelector("[data-credentials]"); if (target) { target.hidden = false; target.innerHTML = `${t("database")}: <code>${esc(data.database)}</code><br>${t("database_user")}: <code>${esc(data.username)}</code><br>${t("password")}: <code>${esc(data.password)}</code>`; } }
async function complete(payload) { if (!payload?.status_url) return payload; const final = await waitForOperation(payload, (state) => { const target = content.querySelector("[data-operation-status]"); if (target) target.innerHTML = `<div class="php-operation__status">${esc(state.stage)} · ${esc(state.status)}</div><div class="php-operation__message">${esc(state.message)}</div>`; }); if (final.status !== "succeeded") throw new Error(final.error || final.message || t("operation_failed")); return final; }

async function handle(action, trigger) {
  const id = siteId;
  let payload;
  if (action === "runtime-submit") payload = await request(actionUrl(id, "/runtime"), "POST", { php_version: content.querySelector("[data-runtime-version]").value });
  if (action === "root-submit") payload = await request(actionUrl(id, "/document-root"), "PATCH", { document_root: content.querySelector("[data-document-root]").value });
  if (action === "control") payload = await request(actionUrl(id, "/control"), "POST", { action: trigger.dataset.value });
  if (action === "repair") payload = await request(actionUrl(id, "/repair"), "POST");
  if (action === "db-create") payload = await request(actionUrl(id, "/database"), "POST", { install_missing_extension: Boolean(content.querySelector("[data-db-install]")?.checked) });
  if (action === "db-reveal") { payload = await request(actionUrl(id, "/database/reveal"), "POST"); showCredentials(payload); return; }
  if (action === "db-rotate") { payload = await request(actionUrl(id, "/database/rotate"), "POST"); await load(); showCredentials(payload); return; }
  if (action === "db-delete") { openDestructiveModal("database"); return; }
  if (action === "ssl-issue") payload = await request(actionUrl(id, "/ssl/issue"), "POST", { include_www: Boolean(content.querySelector("[data-ssl-www]")?.checked) });
  if (action === "ssl-renew") payload = await request(actionUrl(id, "/ssl/renew"), "POST");
  if (action === "ssl-revoke") { openDestructiveModal("ssl"); return; }
  if (action === "wp-retry") { const password = content.querySelector("[data-wp-password]").value; if (!password) throw new Error(t("password_required")); payload = await request(actionUrl(id, "/wordpress/retry"), "POST", { admin_password: password, install_missing_extensions: Boolean(content.querySelector("[data-wp-install]")?.checked) }); }
  if (action === "delete-site") { openDestructiveModal("site"); return; }
  await complete(payload);
  await load();
}

async function submitDestructiveAction() {
  if (!validateDeleteConfirmation()) {
    if (deleteModalError) { deleteModalError.textContent = t("delete_site_confirmation_required"); deleteModalError.hidden = false; }
    deleteConfirm?.focus();
    return;
  }
  const action = pendingDestructiveAction;
  const confirmation = action.expected;
  const deleteDatabase = deleteDatabaseChoice?.querySelector("input:checked")?.value === "true";
  deleteSubmit.disabled = true;
  if (deleteModalError) { deleteModalError.textContent = ""; deleteModalError.hidden = true; }
  try {
    const body = action.kind === "site" ? { confirmation, delete_database: deleteDatabase } : { confirmation };
    const endpoint = action.kind === "site" ? actionUrl(siteId) : action.kind === "database" ? actionUrl(siteId, "/database") : actionUrl(siteId, "/ssl");
    const payload = await request(endpoint, "DELETE", body);
    await complete(payload);
    if (action.kind === "site") window.location.assign("/php-sites/");
    else { closeDeleteModal(); await load(); }
  } catch (err) {
    if (deleteModalError) { deleteModalError.textContent = err.message; deleteModalError.hidden = false; }
    deleteSubmit.disabled = false;
  }
}

async function load() {
  loading.hidden = false;
  try { [site, options] = await Promise.all([request(`/sites/${encodeURIComponent(siteId)}`), request("/options")]); render(); content.hidden = false; loading.hidden = true; if (site.operation && ["queued", "running"].includes(site.operation.status)) { await complete({ status_url: `/api/php-sites/operations/${site.operation.id}` }); await load(); } } catch (err) { loading.hidden = true; setError(err.message); }
}

root?.addEventListener("click", async (event) => {
  const close = event.target.closest("[data-php-site-delete-close]");
  if (close) { closeDeleteModal(); return; }
  if (event.target === deleteModal) { closeDeleteModal(); return; }
  const trigger = event.target.closest("[data-action]");
  if (!trigger) return;
  const destructiveAction = { "delete-site": "site", "db-delete": "database", "ssl-revoke": "ssl" }[trigger.dataset.action];
  if (destructiveAction) { openDestructiveModal(destructiveAction); return; }
  trigger.disabled = true;
  setError("");
  try { await handle(trigger.dataset.action, trigger); } catch (err) { setError(err.message); trigger.disabled = false; }
});

deleteConfirm?.addEventListener("input", validateDeleteConfirmation);
deleteSubmit?.addEventListener("click", submitDestructiveAction);
if (root) load();
