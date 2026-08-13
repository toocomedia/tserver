import { actionUrl, esc, request, statusTone, t, waitForOperation } from "./php-sites-api.js";
import { bindLogEvents, loadLogs, renderLogSection } from "./php-sites-logs.js";

const root = document.querySelector("[data-php-site-detail]");
const siteId = root?.dataset.siteId;
const loading = root?.querySelector("[data-detail-loading]");
const content = root?.querySelector("[data-detail-content]");
const errorEl = root?.querySelector("[data-detail-error]");
const deleteModal = root?.querySelector("[data-php-site-delete-modal]");
const deleteModalTitle = root?.querySelector("[data-php-site-delete-title]");
const deleteModalDesc = root?.querySelector("[data-php-site-delete-description]");
const deleteConfirm = root?.querySelector("[data-php-site-delete-confirm]");
const deleteConfirmText = root?.querySelector("[data-php-site-delete-confirmation]");
const deleteDbChoice = root?.querySelector("[data-php-site-delete-database]");
const deleteModalErr = root?.querySelector("[data-php-site-delete-error]");
const deleteSubmit = root?.querySelector("[data-php-site-delete-submit]");
let site, options, pendingAction;
let currentTab = "overview";
const validTabs = ["overview", "runtime", "database", "ssl", "logs", "danger"];

function setError(msg) { errorEl.textContent = msg || ""; errorEl.hidden = !msg; }
function badge(val) { return `<span class="status-badge status-badge--${statusTone(val)}">${esc(val || "—")}</span>`; }
function can(act) { return Boolean(site?.available_actions?.[act]); }
function btn(act, lbl, tone = "secondary", extra = "") {
  return `<button id="act-${act}" class="btn btn--${tone} btn--sm" type="button" data-action="${act}" ${extra}>${esc(lbl)}</button>`;
}
function row(lbl, val) {
  return `<div class="info-row-strict"><div class="info-row-strict__label">${esc(lbl)}</div><div class="info-row-strict__val">${val}</div></div>`;
}
function sect(title, body, extra = "") {
  return `<section class="info-section php-detail__section ${extra}"><div class="info-section-header"><h3>${esc(title)}</h3></div><div class="php-detail__section-body">${body}</div></section>`;
}

function statGrid() {
  const h = site.health || {};
  const dot = (ok) => `<span class="stat-dot ${ok ? "stat-dot--active" : "stat-dot--danger"}"></span>`;
  const httpCode = h.http?.status_code ? `HTTP ${h.http.status_code}` : t("not_checked");
  return `
    <div class="stat-grid-strict mb-lg">
      <div class="stat-card-strict"><div class="stat-card-strict__title">${esc(t("php_fpm_socket"))}</div><div class="stat-card-strict__value">${dot(h.socket_healthy)} ${h.socket_healthy ? t("active_1") : t("disabled")}</div></div>
      <div class="stat-card-strict"><div class="stat-card-strict__title">${esc(t("nginx_web_engine"))}</div><div class="stat-card-strict__value">${dot(h.nginx_active)} ${h.nginx_active ? t("active_1") : t("disabled")}</div></div>
      <div class="stat-card-strict"><div class="stat-card-strict__title">${esc(t("local_http"))}</div><div class="stat-card-strict__value">${esc(httpCode)}</div></div>
      <div class="stat-card-strict"><div class="stat-card-strict__title">${esc(t("database"))}</div><div class="stat-card-strict__value">${site.database ? `${dot(h.mariadb_healthy)} ${site.database.database}` : `<span style="color:var(--color-muted);">${esc(t("no_database_attached"))}</span>`}</div></div>
      <div class="stat-card-strict"><div class="stat-card-strict__title">${esc(t("ssl_certificates"))}</div><div class="stat-card-strict__value">${site.ssl?.active ? `${dot(true)} ${t("active_1")}` : `<span style="color:var(--color-muted);">${esc(t("not_available"))}</span>`}</div></div>
    </div>
  `;
}

function tabsNav() {
  return `
    <nav class="tabs-nav mb-lg" data-detail-tabs role="tablist">
      <button class="tabs-nav__btn ${currentTab === "overview" ? "is-active" : ""}" type="button" role="tab" data-tab-target="overview" id="tab-btn-overview">${esc(t("overview"))}</button>
      <button class="tabs-nav__btn ${currentTab === "runtime" ? "is-active" : ""}" type="button" role="tab" data-tab-target="runtime" id="tab-btn-runtime">${esc(t("runtime_settings"))}</button>
      <button class="tabs-nav__btn ${currentTab === "database" ? "is-active" : ""}" type="button" role="tab" data-tab-target="database" id="tab-btn-database">${esc(t("database"))}</button>
      <button class="tabs-nav__btn ${currentTab === "ssl" ? "is-active" : ""}" type="button" role="tab" data-tab-target="ssl" id="tab-btn-ssl">${esc(t("ssl_certificates"))}</button>
      <button class="tabs-nav__btn ${currentTab === "logs" ? "is-active" : ""}" type="button" role="tab" data-tab-target="logs" id="tab-btn-logs">${esc(t("logs"))}</button>
      <button class="tabs-nav__btn ${currentTab === "danger" ? "is-active" : ""}" type="button" role="tab" data-tab-target="danger" id="tab-btn-danger" style="color:var(--color-danger);">${esc(t("danger_zone"))}</button>
    </nav>
  `;
}

function runtimeSection() {
  const vers = (options?.php_versions || []).map((i) => `<option value="${esc(i.version)}" ${i.version === site.php_version ? "selected" : ""}>${esc(i.version)}</option>`).join("");
  const vSelect = options ? `<select id="php-runtime-ver" class="form-select" data-runtime-version>${vers}</select>` : `<select class="form-select" disabled><option>${t("loading")}</option></select>`;
  return sect(t("runtime_settings"), `
    ${row(t("php_version"), `<div class="php-detail__inline">${vSelect}${can("change_php_version") ? btn("runtime-submit", t("change"), "secondary") : ""}</div>`)}
    ${row(t("document_root"), `<div class="php-detail__inline"><input id="php-doc-root" class="form-input" data-document-root value="${esc(site.document_root)}" pattern="[A-Za-z0-9][A-Za-z0-9._\\-/]*">${can("change_document_root") ? btn("root-submit", t("change"), "secondary") : ""}</div>`)}
  `);
}

function databaseSection() {
  if (!site.database) {
    return sect(t("database"), `<p class="form-hint">${t("no_database_attached")}</p>${can("create_database") ? `<div class="form-actions mt-md"><label class="form-check"><input type="checkbox" data-db-install checked><span>${t("install_missing_extensions")}</span></label>${btn("db-create", t("create_database_for_site"), "primary")}</div>` : ""}`);
  }
  const db = site.database;
  return sect(t("database"), `
    ${row(t("database_name"), `<code>${esc(db.database)}</code>`)}
    ${row(t("admin_user"), `<code>${esc(db.username)}</code>`)}
    ${row(t("host"), `<code>${esc(db.host)}:${esc(db.port)}</code>`)}
    ${row(t("status"), badge(db.status))}
    <div class="form-actions php-detail__controls mt-md">
      ${btn("db-reveal", t("reveal_credentials"))}
      ${btn("db-rotate", t("rotate_password"))}
      ${can("delete_database") ? btn("db-delete", t("delete_database"), "danger") : ""}
    </div>
    <div class="php-detail__credentials" data-credentials hidden></div>
  `);
}

function sslSection() {
  const ssl = site.ssl || {};
  const act = can("issue_ssl") ? btn("ssl-issue", t("issue_ssl"), "primary") : `${can("renew_ssl") ? btn("ssl-renew", t("renew")) : ""}${can("revoke_ssl") ? btn("ssl-revoke", t("revoke"), "danger") : ""}`;
  return sect(t("ssl_certificates"), `
    ${row(t("status"), badge(ssl.active ? "active" : "inactive"))}
    ${row(t("expires"), esc(ssl.expiry_date || t("not_available")))}
    <div class="form-actions php-detail__controls mt-md">
      <label class="form-check"><input type="checkbox" data-ssl-www ${ssl.include_www ? "checked" : ""}><span>${t("include_www")}</span></label>
      ${act}
    </div>
  `);
}

function wpSection() {
  if (site.preset !== "wordpress") return "";
  const wp = site.wordpress || {};
  const retry = can("wordpress_retry") ? `
    <div class="form-actions php-detail__controls mt-md">
      <div class="form-group php-detail__password"><label class="form-label">${t("new_admin_password")}</label><input class="form-input" data-wp-password type="password" minlength="12"></div>
      <label class="form-check"><input type="checkbox" data-wp-install checked><span>${t("install_missing_extensions")}</span></label>
      ${btn("wp-retry", t("retry"), "primary")}
    </div>
  ` : "";
  return sect(t("wordpress_settings"), `
    ${row(t("site_title"), esc(wp.site_title))}
    ${row(t("admin_user"), esc(wp.admin_user))}
    ${row(t("admin_email"), esc(wp.admin_email))}
    ${row(t("status"), badge(wp.installed ? "active" : "failed"))}
    ${retry}
  `);
}

function switchTab(tabId) {
  if (!validTabs.includes(tabId)) tabId = "overview";
  currentTab = tabId;
  try {
    history.replaceState(null, "", `#${tabId}`);
  } catch (_) {
    window.location.hash = tabId;
  }

  content.querySelectorAll("[data-tab-target]").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.tabTarget === tabId);
  });

  content.querySelectorAll(".php-tab-pane").forEach((pane) => {
    pane.classList.toggle("is-active", pane.dataset.tabPanel === tabId);
  });

  if (tabId === "logs") {
    loadLogs(siteId, content);
  }
}

function render() {
  const hash = (window.location.hash || "").replace("#", "");
  if (validTabs.includes(hash)) currentTab = hash;

  const url = `${site.ssl?.active ? "https" : "http"}://${site.domain}/`;
  let actions = `<a class="btn btn--secondary btn--sm" href="${esc(url)}" target="_blank" rel="noopener">${t("visit_website")} ↗</a>`;
  if (site.file_manager_target) actions += `<a class="btn btn--secondary btn--sm" href="/plugins/file_manager/?target=${encodeURIComponent(site.file_manager_target)}">${t("file_manager")}</a>`;
  if (can("enable")) actions += btn("control", t("enable"), "primary", 'data-value="enable"');
  if (can("disable")) actions += btn("control", t("disable"), "secondary", 'data-value="disable"');
  if (can("repair")) actions += btn("repair", t("repair"), "primary");
  if (can("restore")) actions += btn("restore", t("restore_website"), "primary");

  const isWp = site.preset === "wordpress";

  content.innerHTML = `
    <div class="page-header-strict mb-lg">
      <div>
        <h1 style="margin-bottom: 4px;">${esc(site.domain)} ${badge(site.status)}</h1>
        <span style="color:var(--color-muted); font-size:14px;">PHP Website Manager · ${esc(isWp ? t("wordpress") : t("plain_php"))}</span>
      </div>
      <div class="actions" style="display:flex; gap:8px; flex-wrap:wrap;">${actions}</div>
    </div>

    ${site.last_error ? `<div class="alert alert--danger mb-lg">${esc(site.last_error)}</div>` : ""}
    ${site.last_warning ? `<div class="alert alert--warning mb-lg">${esc(site.last_warning)}</div>` : ""}
    ${site.operation && ["queued", "running"].includes(site.operation.status) ? `<div class="php-operation mb-lg"><div class="php-operation__status">${esc(site.operation.stage)} · ${esc(site.operation.status)}</div><div class="php-operation__message">${esc(site.operation.message)}</div></div>` : ""}

    ${statGrid()}
    ${tabsNav()}

    <!-- TAB 1: OVERVIEW -->
    <div class="php-tab-pane ${currentTab === "overview" ? "is-active" : ""}" data-tab-panel="overview">
      <div class="php-detail__grid">
        ${sect(t("overview"), `
          ${row(t("domain"), `<a href="${esc(url)}" target="_blank" rel="noopener" style="font-weight:700; color:var(--color-text);">${esc(site.domain)} ↗</a>`)}
          ${row(t("preset"), `<span class="badge-pill">${esc(isWp ? t("wordpress") : t("plain_php"))}</span>`)}
          ${row(t("status"), badge(site.status))}
          ${row(t("linux_user"), `<code>srvphp${site.id}</code>`)}
          ${row(t("webroot"), `<code>/var/www/${esc(site.domain)}/${esc(site.document_root)}</code>`)}
        `)}
        ${sect(t("runtime_settings"), `
          ${row(t("php_version"), `<strong>${esc(site.php_version || "—")}</strong>`)}
          ${row(t("document_root"), `<code>${esc(site.document_root || "public")}</code>`)}
          ${row(t("database"), site.database ? `<code>${esc(site.database.database)}</code> (${esc(site.database.status || "active")})` : `<span style="color:var(--color-muted);">${esc(t("no_database_attached"))}</span>`)}
          ${row(t("ssl_certificates"), site.ssl?.active ? `${badge("active")} ${site.ssl.expiry_date ? `(Expires ${esc(site.ssl.expiry_date)})` : ""}` : `<span style="color:var(--color-muted);">${esc(t("not_available"))}</span>`)}
        `)}
      </div>
    </div>

    <!-- TAB 2: RUNTIME SETTINGS -->
    <div class="php-tab-pane ${currentTab === "runtime" ? "is-active" : ""}" data-tab-panel="runtime">
      <div class="${isWp ? "php-detail__grid" : ""}">
        ${runtimeSection()}
        ${isWp ? wpSection() : ""}
      </div>
    </div>

    <!-- TAB 3: DATABASE -->
    <div class="php-tab-pane ${currentTab === "database" ? "is-active" : ""}" data-tab-panel="database">
      ${databaseSection()}
    </div>

    <!-- TAB 4: SSL CERTIFICATES -->
    <div class="php-tab-pane ${currentTab === "ssl" ? "is-active" : ""}" data-tab-panel="ssl">
      ${sslSection()}
    </div>

    <!-- TAB 5: LOGS -->
    <div class="php-tab-pane ${currentTab === "logs" ? "is-active" : ""}" data-tab-panel="logs">
      ${renderLogSection()}
    </div>

    <!-- TAB 6: DANGER ZONE -->
    <div class="php-tab-pane ${currentTab === "danger" ? "is-active" : ""}" data-tab-panel="danger">
      ${sect(t("danger_zone"), `
        <p class="form-hint mb-lg" style="max-width:600px; line-height:1.6;">${t("delete_php_site_desc")}</p>
        <div style="display:flex; gap:12px; flex-wrap:wrap;">
          ${can("archive") ? btn("archive-site", t("archive_website"), "secondary") : ""}
          ${can("delete_site") ? btn("delete-site", t("delete_website"), "danger") : ""}
        </div>
      `, "php-detail__danger")}
    </div>
  `;

  bindLogEvents(siteId, content);
  if (currentTab === "logs") {
    loadLogs(siteId, content);
  }
}

function openModal(kind) {
  if (!site || !deleteModal) return;
  const db = site.database?.database;
  const map = {
    site: { title: t("delete_website"), desc: `${t("delete_php_site_desc")} ${site.domain}`, exp: `DELETE ${site.domain}` },
    database: { title: t("delete_database"), desc: `${t("delete_database_confirmation_desc")} ${db}`, exp: `DELETE DATABASE ${db}` },
    ssl: { title: t("revoke"), desc: `${t("revoke_ssl_confirmation_desc")} ${site.domain}`, exp: `REVOKE ${site.domain}` },
    archive: { title: t("archive_website"), desc: `${t("archive_confirmation_desc")} ARCHIVE ${site.domain}`, exp: `ARCHIVE ${site.domain}` },
  };
  pendingAction = { kind, ...map[kind] };
  if (deleteModalTitle) deleteModalTitle.textContent = pendingAction.title;
  if (deleteModalDesc) deleteModalDesc.textContent = pendingAction.desc;
  if (deleteConfirmText) deleteConfirmText.textContent = pendingAction.exp;
  if (deleteDbChoice) deleteDbChoice.hidden = kind !== "site" || !site.database;
  if (deleteSubmit) { deleteSubmit.textContent = pendingAction.title; deleteSubmit.disabled = true; }
  if (deleteConfirm) deleteConfirm.value = "";
  deleteModal.classList.remove("hidden");
  deleteConfirm?.focus();
}

function closeModal() {
  deleteModal?.classList.add("hidden");
  if (deleteConfirm) deleteConfirm.value = "";
  if (deleteModalErr) { deleteModalErr.textContent = ""; deleteModalErr.hidden = true; }
  pendingAction = null;
}

async function handle(action, trigger) {
  const id = siteId;
  let payload;
  if (action === "runtime-submit") payload = await request(actionUrl(id, "/runtime"), "POST", { php_version: content.querySelector("[data-runtime-version]").value });
  if (action === "root-submit") payload = await request(actionUrl(id, "/document-root"), "PATCH", { document_root: content.querySelector("[data-document-root]").value });
  if (action === "control") payload = await request(actionUrl(id, "/control"), "POST", { action: trigger.dataset.value });
  if (action === "repair") payload = await request(actionUrl(id, "/repair"), "POST");
  if (action === "restore") payload = await request(actionUrl(id, "/restore"), "POST");
  if (action === "db-create") payload = await request(actionUrl(id, "/database"), "POST", { install_missing_extension: Boolean(content.querySelector("[data-db-install]")?.checked) });
  if (action === "db-reveal") {
    const creds = await request(actionUrl(id, "/database/reveal"), "POST");
    const target = content.querySelector("[data-credentials]");
    if (target) {
      target.hidden = false;
      target.innerHTML = `<div style="font-weight:700; margin-bottom:4px;">${t("credentials_revealed")}</div>${t("database")}: <code>${esc(creds.database)}</code> | ${t("admin_user")}: <code>${esc(creds.username)}</code> | ${t("password")}: <code>${esc(creds.password)}</code>`;
    }
    return;
  }
  if (action === "db-rotate") {
    const creds = await request(actionUrl(id, "/database/rotate"), "POST");
    await load();
    const target = content.querySelector("[data-credentials]");
    if (target) {
      target.hidden = false;
      target.innerHTML = `<div style="font-weight:700; margin-bottom:4px;">${t("credentials_revealed")} (Rotated)</div>${t("database")}: <code>${esc(creds.database)}</code> | ${t("admin_user")}: <code>${esc(creds.username)}</code> | ${t("password")}: <code>${esc(creds.password)}</code>`;
    }
    return;
  }
  if (action === "ssl-issue") payload = await request(actionUrl(id, "/ssl/issue"), "POST", { include_www: Boolean(content.querySelector("[data-ssl-www]")?.checked) });
  if (action === "ssl-renew") payload = await request(actionUrl(id, "/ssl/renew"), "POST");
  if (action === "wp-retry") {
    const pass = content.querySelector("[data-wp-password]")?.value;
    if (!pass) throw new Error(t("password_required"));
    payload = await request(actionUrl(id, "/wordpress/retry"), "POST", { admin_password: pass, install_missing_extensions: Boolean(content.querySelector("[data-wp-install]")?.checked) });
  }
  if (payload?.status_url) await waitForOperation(payload);
  await load();
}

async function submitModal() {
  if (!pendingAction || deleteConfirm.value.trim() !== pendingAction.exp) return;
  const { kind, exp } = pendingAction;
  deleteSubmit.disabled = true;
  try {
    if (kind === "site") {
      const dropDb = deleteDbChoice?.querySelector("input:checked")?.value === "true";
      const payload = await request(actionUrl(siteId), "DELETE", { confirmation: exp, delete_database: dropDb });
      if (payload?.status_url) await waitForOperation(payload);
      window.location.assign("/php-sites/");
      return;
    }
    if (kind === "database") await request(actionUrl(siteId, "/database"), "DELETE", { confirmation: exp });
    if (kind === "ssl") {
      const payload = await request(actionUrl(siteId, "/ssl"), "DELETE", { confirmation: exp });
      if (payload?.status_url) await waitForOperation(payload);
    }
    if (kind === "archive") {
      const payload = await request(actionUrl(siteId, "/archive"), "POST", { confirmation: exp });
      if (payload?.status_url) await waitForOperation(payload);
    }
    closeModal();
    await load();
  } catch (err) {
    if (deleteModalErr) { deleteModalErr.textContent = err.message; deleteModalErr.hidden = false; }
    deleteSubmit.disabled = false;
  }
}

async function load() {
  if (!site) loading.hidden = false;
  try {
    site = await request(`/sites/${encodeURIComponent(siteId)}`);
    render();
    content.hidden = false;
    loading.hidden = true;
    if (window.hideSkeleton) window.hideSkeleton("php-site-detail-skeleton", 0);
    if (!options) { options = await request("/options"); render(); }
  } catch (err) {
    loading.hidden = true;
    if (window.hideSkeleton) window.hideSkeleton("php-site-detail-skeleton", 0);
    setError(err.message);
  }
}

root?.addEventListener("click", async (e) => {
  const tabBtn = e.target.closest("[data-tab-target]");
  if (tabBtn) {
    switchTab(tabBtn.dataset.tabTarget);
    return;
  }
  if (e.target.closest("[data-php-site-delete-close]") || e.target === deleteModal) { closeModal(); return; }
  const trigger = e.target.closest("[data-action]");
  if (!trigger) return;
  const kind = { "delete-site": "site", "db-delete": "database", "ssl-revoke": "ssl", "archive-site": "archive" }[trigger.dataset.action];
  if (kind) { openModal(kind); return; }
  trigger.disabled = true;
  setError("");
  try { await handle(trigger.dataset.action, trigger); }
  catch (err) { setError(err.message); trigger.disabled = false; }
});

window.addEventListener("hashchange", () => {
  const hash = window.location.hash.replace("#", "");
  if (validTabs.includes(hash)) switchTab(hash);
});

deleteConfirm?.addEventListener("input", () => {
  deleteSubmit.disabled = deleteConfirm.value.trim() !== pendingAction?.exp;
});
deleteSubmit?.addEventListener("click", submitModal);

if (root) load();
