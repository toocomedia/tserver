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
  return `<div class="info-row"><div class="info-row__label">${esc(lbl)}</div><div class="info-row__value">${val}</div></div>`;
}

function tabsNav() {
  return `
    <nav class="tabs-nav tabs-nav--opacity mb-lg" data-detail-tabs role="tablist">
      <button class="tabs-nav__btn ${currentTab === "overview" ? "is-active" : ""}" type="button" role="tab" data-tab-target="overview" id="tab-btn-overview">${esc(t("overview"))}</button>
      <button class="tabs-nav__btn ${currentTab === "runtime" ? "is-active" : ""}" type="button" role="tab" data-tab-target="runtime" id="tab-btn-runtime">${esc(t("runtime_settings"))}</button>
      <button class="tabs-nav__btn ${currentTab === "database" ? "is-active" : ""}" type="button" role="tab" data-tab-target="database" id="tab-btn-database">${esc(t("database"))}</button>
      <button class="tabs-nav__btn ${currentTab === "ssl" ? "is-active" : ""}" type="button" role="tab" data-tab-target="ssl" id="tab-btn-ssl">${esc(t("ssl_certificates"))}</button>
      <button class="tabs-nav__btn ${currentTab === "logs" ? "is-active" : ""}" type="button" role="tab" data-tab-target="logs" id="tab-btn-logs">${esc(t("logs"))}</button>
      <button class="tabs-nav__btn tabs-nav__btn--danger ${currentTab === "danger" ? "is-active" : ""}" type="button" role="tab" data-tab-target="danger" id="tab-btn-danger">${esc(t("danger_zone"))}</button>
    </nav>
  `;
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

  content.querySelectorAll(".tabs-panel").forEach((pane) => {
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
  const isWp = site.preset === "wordpress";
  const wp = site.wordpress || {};
  const h = site.health || {};
  const dot = (ok) => `<span class="stat-dot ${ok ? "stat-dot--active" : "stat-dot--danger"}"></span>`;
  const httpCode = h.http?.status_code ? `HTTP ${h.http.status_code}` : t("not_checked");

  const titleEl = document.getElementById("php-site-title");
  if (titleEl) {
    titleEl.innerHTML = `${esc(site.domain)} ${badge(site.status)}`;
  }

  const topbarActionsEl = document.getElementById("php-site-topbar-actions");
  if (topbarActionsEl) {
    let actHtml = `<a class="btn btn--secondary btn--sm" href="${esc(url)}" target="_blank" rel="noopener">${t("visit_website")} ↗</a>`;
    if (site.file_manager_target) actHtml += `<a class="btn btn--secondary btn--sm" href="/plugins/file_manager/?target=${encodeURIComponent(site.file_manager_target)}">${t("file_manager")}</a>`;
    if (can("enable")) actHtml += btn("control", t("enable"), "primary", 'data-value="enable"');
    if (can("disable")) actHtml += btn("control", t("disable"), "secondary", 'data-value="disable"');
    if (can("repair")) actHtml += btn("repair", t("repair"), "primary");
    if (can("restore")) actHtml += btn("restore", t("restore_website"), "primary");
    topbarActionsEl.innerHTML = actHtml;
  }

  const vers = (options?.php_versions || []).map((i) => `<option value="${esc(i.version)}" ${i.version === site.php_version ? "selected" : ""}>${esc(i.version)}</option>`).join("");
  const vSelect = options ? `<select id="php-runtime-ver" class="form-select" data-runtime-version style="width:160px; height:32px;">${vers}</select>` : `<select class="form-select" disabled style="width:160px; height:32px;"><option>${t("loading")}</option></select>`;

  const ssl = site.ssl || {};
  const sslAct = can("issue_ssl") ? btn("ssl-issue", t("issue_ssl"), "primary") : `${can("renew_ssl") ? btn("ssl-renew", t("renew")) : ""}${can("revoke_ssl") ? btn("ssl-revoke", t("revoke"), "danger") : ""}`;

  const wpRetry = can("wordpress_retry") ? `
    <div class="d-flex align-center gap-sm" style="flex-wrap:wrap;">
      <input class="form-input" data-wp-password type="password" minlength="12" placeholder="${esc(t("new_admin_password"))}" style="width:220px; height:32px;">
      <label class="form-check" style="margin:0;"><input type="checkbox" data-wp-install checked><span>${t("install_missing_extensions")}</span></label>
      ${btn("wp-retry", t("retry"), "primary")}
    </div>
  ` : "";

  const sslExp = ssl.expiry_date ? ssl.expiry_date.replace("T", " ").slice(0, 16) : "";

  content.innerHTML = `
    ${site.last_error ? `<div class="alert alert--danger mb-lg">${esc(site.last_error)}</div>` : ""}
    ${site.last_warning ? `<div class="alert alert--warning mb-lg">${esc(site.last_warning)}</div>` : ""}
    ${site.operation && ["queued", "running"].includes(site.operation.status) ? `<div class="php-operation mb-lg"><div class="php-operation__status">${esc(site.operation.stage)} · ${esc(site.operation.status)}</div><div class="php-operation__message">${esc(site.operation.message)}</div></div>` : ""}

    ${tabsNav()}

    <!-- TAB 1: OVERVIEW -->
    <div class="tabs-panel ${currentTab === "overview" ? "is-active" : ""}" data-tab-panel="overview">
      <div class="section mb-xl">
        <div class="section__header"><h3>${esc(t("overview"))}</h3></div>
        <div class="section__body">
          ${row(t("domain"), `<a href="${esc(url)}" target="_blank" rel="noopener" style="font-weight:700; color:var(--color-text);">${esc(site.domain)} ↗</a>`)}
          ${row(t("preset"), `<span class="badge-pill">${esc(isWp ? t("wordpress") : t("plain_php"))}</span>`)}
          ${row(t("status"), badge(site.status))}
          ${row(t("linux_user"), `<code>srvphp${site.id}</code>`)}
          ${row(t("document_root"), `<code>${esc(site.document_root || "public")}</code>`)}
          ${row(t("webroot"), `<code>/var/www/${esc(site.domain)}/${esc(site.document_root)}</code>`)}
          ${row(t("php_version"), `<strong>${esc(site.php_version || "—")}</strong>`)}
          ${row(t("php_fpm_socket"), `${dot(h.socket_healthy)} <strong>${h.socket_healthy ? t("active_1") : t("disabled")}</strong>`)}
          ${row(t("nginx_web_engine"), `${dot(h.nginx_active)} <strong>${h.nginx_active ? t("active_1") : t("disabled")}</strong>`)}
          ${row(t("local_http"), `<strong>${esc(httpCode)}</strong>`)}
          ${row(t("database"), site.database ? `${dot(h.mariadb_healthy)} <code>${esc(site.database.database)}</code>` : `<span style="color:var(--color-muted);">${esc(t("no_database_attached"))}</span>`)}
          ${row(t("ssl_certificates"), site.ssl?.active ? `${dot(true)} <strong>${t("active_1")}</strong>${sslExp ? ` <span style="font-size:12px; color:var(--color-muted);">(Expires ${esc(sslExp)})</span>` : ""}` : `<span style="color:var(--color-muted);">${esc(t("not_available"))}</span>`)}
        </div>
      </div>
    </div>

    <!-- TAB 2: RUNTIME SETTINGS -->
    <div class="tabs-panel ${currentTab === "runtime" ? "is-active" : ""}" data-tab-panel="runtime">
      <div class="section mb-xl">
        <div class="section__header"><h3>${esc(t("runtime_settings"))}</h3></div>
        <div class="section__body">
          ${row(t("php_version"), `<div class="d-flex align-center gap-sm" style="flex-wrap:wrap;">${vSelect}${can("change_php_version") ? btn("runtime-submit", t("change"), "secondary") : ""}</div>`)}
          ${row(t("document_root"), `<div class="d-flex align-center gap-sm" style="flex-wrap:wrap;"><input id="php-doc-root" class="form-input" data-document-root value="${esc(site.document_root)}" pattern="[A-Za-z0-9][A-Za-z0-9._\\-/]*" style="width:200px; height:32px;">${can("change_document_root") ? btn("root-submit", t("change"), "secondary") : ""}</div>`)}
        </div>
      </div>

      ${isWp ? `
        <div class="section mb-xl">
          <div class="section__header"><h3>${esc(t("wordpress_settings"))}</h3></div>
          <div class="section__body">
            ${row(t("site_title"), esc(wp.site_title || "—"))}
            ${row(t("admin_user"), `<code>${esc(wp.admin_user || "—")}</code>`)}
            ${row(t("admin_email"), esc(wp.admin_email || "—"))}
            ${row(t("status"), badge(wp.installed ? "active" : "failed"))}
            ${wpRetry ? row(t("retry_setup") || "Retry Setup", wpRetry) : ""}
          </div>
        </div>
      ` : ""}
    </div>

    <!-- TAB 3: DATABASE -->
    <div class="tabs-panel ${currentTab === "database" ? "is-active" : ""}" data-tab-panel="database">
      <div class="section mb-xl">
        <div class="section__header"><h3>${esc(t("database"))}</h3></div>
        <div class="section__body">
          ${site.database ? `
            ${row(t("database_name"), `<code>${esc(site.database.database)}</code>`)}
            ${row(t("admin_user"), `<code>${esc(site.database.username)}</code>`)}
            ${row(t("host"), `<code>${esc(site.database.host)}:${esc(site.database.port)}</code>`)}
            ${row(t("status"), badge(site.database.status))}
            ${row(t("actions") || "Actions", `
              <div class="d-flex align-center gap-sm" style="flex-wrap:wrap;">
                ${btn("db-reveal", t("reveal_credentials"))}
                ${btn("db-rotate", t("rotate_password"))}
                ${can("delete_database") ? btn("db-delete", t("delete_database"), "danger") : ""}
              </div>
            `)}
            <div class="php-detail__credentials" data-credentials hidden style="margin: 16px 0;"></div>
          ` : `
            <div style="padding: 16px 0;">
              <p class="form-hint" style="margin: 0 0 16px 0;">${esc(t("no_database_attached"))}</p>
              ${can("create_database") ? `
                <div class="d-flex align-center gap-md" style="flex-wrap:wrap;">
                  <label class="form-check" style="margin:0;"><input type="checkbox" data-db-install checked><span>${t("install_missing_extensions")}</span></label>
                  ${btn("db-create", t("create_database_for_site"), "primary")}
                </div>
              ` : ""}
            </div>
          `}
        </div>
      </div>
    </div>

    <!-- TAB 4: SSL CERTIFICATES -->
    <div class="tabs-panel ${currentTab === "ssl" ? "is-active" : ""}" data-tab-panel="ssl">
      <div class="section mb-xl">
        <div class="section__header"><h3>${esc(t("ssl_certificates"))}</h3></div>
        <div class="section__body">
          ${row(t("status"), badge(ssl.active ? "active" : "inactive"))}
          ${row(t("expires"), esc(sslExp || t("not_available")))}
          ${row(t("include_www") || "Include WWW", `
            <label class="form-check" style="margin:0;"><input type="checkbox" data-ssl-www ${ssl.include_www ? "checked" : ""}><span>${esc(t("include_www"))}</span></label>
          `)}
          ${row(t("actions") || "Actions", `
            <div class="d-flex align-center gap-sm" style="flex-wrap:wrap;">
              ${sslAct}
            </div>
          `)}
        </div>
      </div>
    </div>

    <!-- TAB 5: LOGS -->
    <div class="tabs-panel ${currentTab === "logs" ? "is-active" : ""}" data-tab-panel="logs">
      <div class="section mb-xl">
        <div class="section__header" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
          <h3>${esc(t("logs"))}</h3>
          <div class="d-flex align-center gap-sm" style="flex-wrap:wrap;">
            <div class="php-log-tabs d-flex gap-xs">
              <button type="button" class="btn btn--sm btn--secondary php-log-tab is-active" data-log-stream="access">${esc(t("access_log"))}</button>
              <button type="button" class="btn btn--sm btn--secondary php-log-tab" data-log-stream="nginx_error">${esc(t("nginx_error_log"))}</button>
              <button type="button" class="btn btn--sm btn--secondary php-log-tab" data-log-stream="php">${esc(t("php_fpm_log"))}</button>
            </div>
            <select class="form-select php-log-lines" data-log-lines style="height:30px; font-size:12px; padding:0 8px; width:auto;">
              <option value="50">50 ${esc(t("lines"))}</option>
              <option value="100" selected>100 ${esc(t("lines"))}</option>
              <option value="200">200 ${esc(t("lines"))}</option>
              <option value="500">500 ${esc(t("lines"))}</option>
            </select>
            <button type="button" class="btn btn--sm btn--secondary" data-log-refresh>${esc(t("refresh"))}</button>
          </div>
        </div>
        <div class="section__body" style="padding-top: 16px;">
          <pre class="php-log-terminal" data-log-terminal style="margin:0;">${esc(t("loading"))}</pre>
        </div>
      </div>
    </div>

    <!-- TAB 6: DANGER ZONE -->
    <div class="tabs-panel ${currentTab === "danger" ? "is-active" : ""}" data-tab-panel="danger">
      <div class="section mb-xl" style="border-color: var(--color-danger);">
        <div class="section__header" style="border-color: var(--color-danger);"><h3 style="color:var(--color-danger);">${esc(t("danger_zone"))}</h3></div>
        <div class="section__body" style="padding: 24px 0;">
          <p class="form-hint mb-lg" style="max-width:600px; line-height:1.6; margin:0 0 16px 0;">${esc(t("delete_php_site_desc"))}</p>
          <div class="d-flex align-center gap-md" style="flex-wrap:wrap;">
            ${can("archive") ? btn("archive-site", t("archive_website"), "secondary") : ""}
            ${can("delete_site") ? btn("delete-site", t("delete_website"), "danger") : ""}
          </div>
        </div>
      </div>
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
