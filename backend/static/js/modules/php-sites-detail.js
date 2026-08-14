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
function card(title, body, extra = "") {
  return `
    <div class="php-card ${extra}">
      ${title ? `<div class="php-card__title">${esc(title)}</div>` : ""}
      <div class="php-specs">${body}</div>
    </div>
  `;
}

function spec(key, val) {
  return `
    <div class="php-spec-row">
      <div class="php-spec-key">${esc(key)}</div>
      <div class="php-spec-val">${val}</div>
    </div>
  `;
}

const PRESET_OVERVIEW_ROWS = {
  filament: [{ label: "filament_admin_url", path: "/admin" }],
};

function overviewAccessRows(url) {
  const rows = [{ label: "website_root", path: "/" }, ...(PRESET_OVERVIEW_ROWS[site.preset] || [])];
  return rows.map(({ label, path }) => {
    const href = path === "/" ? url : `${url}${path.slice(1)}`;
    return spec(t(label), `<a class="php-site-link" href="${esc(href)}" target="_blank" rel="noopener"><code>${esc(path)}</code><span>${esc(href)} ↗</span></a>`);
  }).join("");
}

function tabsNav() {
  return `
    <div class="php-split-nav" data-detail-tabs role="tablist">
      <button class="php-split-nav-btn ${currentTab === "overview" ? "is-active" : ""}" type="button" role="tab" data-tab-target="overview" id="tab-btn-overview">${esc(t("overview"))}</button>
      <button class="php-split-nav-btn ${currentTab === "runtime" ? "is-active" : ""}" type="button" role="tab" data-tab-target="runtime" id="tab-btn-runtime">${esc(t("runtime_settings"))}</button>
      <button class="php-split-nav-btn ${currentTab === "database" ? "is-active" : ""}" type="button" role="tab" data-tab-target="database" id="tab-btn-database">${esc(t("database"))}</button>
      <button class="php-split-nav-btn ${currentTab === "ssl" ? "is-active" : ""}" type="button" role="tab" data-tab-target="ssl" id="tab-btn-ssl">${esc(t("ssl_certificates"))}</button>
      <button class="php-split-nav-btn ${currentTab === "logs" ? "is-active" : ""}" type="button" role="tab" data-tab-target="logs" id="tab-btn-logs">${esc(t("logs"))}</button>
      <button class="php-split-nav-btn php-split-nav-btn--danger ${currentTab === "danger" ? "is-active" : ""}" type="button" role="tab" data-tab-target="danger" id="tab-btn-danger">${esc(t("danger_zone"))}</button>
    </div>
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
  const isFilament = site.preset === "filament";
  const isLaravel = site.preset === "laravel" || isFilament;
  const wp = site.wordpress || {};
  const h = site.health || {};
  const dot = (ok) => `<span class="stat-dot ${ok ? "stat-dot--active" : "stat-dot--danger"}"></span>`;
  const httpCode = h.http?.status_code ? `HTTP ${h.http.status_code}` : t("not_checked");

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
  const laravelRetry = can("laravel_retry") ? `
    <div class="d-flex align-center gap-sm" style="flex-wrap:wrap;">
      <label class="form-check" style="margin:0;"><input type="checkbox" data-laravel-install checked><span>${t("install_missing_extensions")}</span></label>
      ${btn("laravel-retry", t("retry"), "primary")}
    </div>
  ` : "";
  const filamentRetry = can("filament_retry") ? `
    <div class="d-flex align-center gap-sm" style="flex-wrap:wrap;">
      <input class="form-input" data-filament-name placeholder="${esc(t("name"))}" style="width:160px; height:32px;">
      <input class="form-input" data-filament-email type="email" placeholder="${esc(t("admin_email"))}" style="width:220px; height:32px;">
      <input class="form-input" data-filament-password type="password" minlength="12" placeholder="${esc(t("new_admin_password"))}" style="width:220px; height:32px;">
      <label class="form-check" style="margin:0;"><input type="checkbox" data-filament-install checked><span>${t("install_missing_extensions")}</span></label>
      ${btn("filament-retry", t("retry"), "primary")}
    </div>
  ` : "";

  const sslExp = ssl.expiry_date ? ssl.expiry_date.replace("T", " ").slice(0, 16) : "";

  content.innerHTML = `
    ${site.last_error ? `<div class="alert alert--danger mb-lg">${esc(site.last_error)}</div>` : ""}
    ${site.last_warning ? `<div class="alert alert--warning mb-lg">${esc(site.last_warning)}</div>` : ""}
    ${site.operation && ["queued", "running"].includes(site.operation.status) ? `<div class="php-operation mb-lg"><div class="php-operation__status">${esc(site.operation.stage)} · ${esc(site.operation.status)}</div><div class="php-operation__message">${esc(site.operation.message)}</div></div>` : ""}

    <!-- HERO APP BANNER BOX -->
    <div class="hero-app-box mb-lg">
      <div style="display: flex; align-items: center; gap: 12px;">
        <h1 style="font-size: 22px; font-weight: 700; margin: 0; line-height: 1.2;">${esc(site.domain)}</h1>
        ${badge(site.status)}
      </div>
      <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
        <a class="btn btn--secondary btn--sm" href="${esc(url)}" target="_blank" rel="noopener">${t("visit_website")} ↗</a>
        ${site.file_manager_target ? `<a class="btn btn--secondary btn--sm" href="/plugins/file_manager/?target=${encodeURIComponent(site.file_manager_target)}">${t("file_manager")}</a>` : ""}
        ${can("enable") ? btn("control", t("enable"), "primary", 'data-value="enable"') : ""}
        ${can("disable") ? btn("control", t("disable"), "secondary", 'data-value="disable"') : ""}
        ${can("repair") ? btn("repair", t("repair"), "primary") : ""}
        ${can("restore") ? btn("restore", t("restore_website"), "primary") : ""}
      </div>
    </div>

    <!-- VERTICAL SPLIT CONTAINER (OPTION 4) -->
    <div class="php-split-layout">
      ${tabsNav()}

      <div class="php-split-content">
        <!-- TAB 1: OVERVIEW -->
        <div class="tabs-panel ${currentTab === "overview" ? "is-active" : ""}" data-tab-panel="overview">
          <div class="php-cards-grid">
            ${card(t("site_access"), overviewAccessRows(url))}

            ${card(t("site_details") || "Site Details", `
              ${spec(t("domain"), `<a href="${esc(url)}" target="_blank" rel="noopener" style="font-weight:700; color:var(--color-text);">${esc(site.domain)} ↗</a>`)}
              ${spec(t("preset"), `<span class="badge-pill">${esc(isWp ? t("wordpress") : isFilament ? t("filament") : isLaravel ? t("laravel") : t("plain_php"))}</span>`)}
              ${spec(t("linux_user"), `<code>srvphp${site.id}</code>`)}
              ${spec(t("document_root"), `<code>${esc(site.document_root || "public")}</code>`)}
              ${spec(t("webroot"), `<code>/var/www/${esc(site.domain)}/${esc(site.document_root)}</code>`)}
            `)}

            ${card(t("service_state_health") || "Service State & Health", `
              ${spec(t("php_version"), `<strong>${esc(site.php_version || "—")}</strong>`)}
              ${spec(t("php_fpm_socket"), `${dot(h.socket_healthy)} <strong>${h.socket_healthy ? t("active_1") : t("disabled")}</strong>`)}
              ${spec(t("nginx_web_engine"), `${dot(h.nginx_active)} <strong>${h.nginx_active ? t("active_1") : t("disabled")}</strong>`)}
              ${spec(t("local_http"), `<strong>${esc(httpCode)}</strong>`)}
              ${spec(t("database"), site.database ? `${dot(h.mariadb_healthy)} <code>${esc(site.database.database)}</code> <span style="font-size:12px; color:var(--color-muted);">(${esc(site.database.host)}:${esc(site.database.port)})</span>` : `<span style="color:var(--color-muted);">${esc(t("no_database_attached"))}</span>`)}
              ${spec(t("ssl_certificates"), site.ssl?.active ? `${dot(true)} <strong>${t("active_1")}</strong>${sslExp ? ` <span style="font-size:12px; color:var(--color-muted);">(Expires ${esc(sslExp)})</span>` : ""}` : `<span style="color:var(--color-muted);">${esc(t("not_available"))}</span>`)}
            `)}
          </div>
        </div>

        <!-- TAB 2: RUNTIME SETTINGS -->
        <div class="tabs-panel ${currentTab === "runtime" ? "is-active" : ""}" data-tab-panel="runtime">
          <div class="php-cards-grid">
            ${card(t("runtime_settings"), `
              ${spec(t("php_version"), `<div class="php-inline-control">${vSelect}${can("change_php_version") ? btn("runtime-submit", t("change"), "secondary") : ""}</div>`)}
              ${spec(t("document_root"), isLaravel ? `<code>public</code>` : `<div class="php-inline-control"><input id="php-doc-root" class="form-input" data-document-root value="${esc(site.document_root)}" pattern="[A-Za-z0-9][A-Za-z0-9._\\-/]*" style="width:200px;">${can("change_document_root") ? btn("root-submit", t("change"), "secondary") : ""}</div>`)}
            `)}

            ${isWp ? card(t("wordpress_settings"), `
              ${spec(t("site_title"), esc(wp.site_title || "—"))}
              ${spec(t("admin_user"), `<code>${esc(wp.admin_user || "—")}</code>`)}
              ${spec(t("admin_email"), esc(wp.admin_email || "—"))}
              ${spec(t("status"), badge(wp.installed ? "active" : "failed"))}
              ${wpRetry ? spec(t("retry_setup") || "Retry Setup", wpRetry) : ""}
              ${spec(t("wordpress_installer_cache"), btn("wp-cache-clear", t("clear_wordpress_installer_cache"), "secondary"))}
            `) : ""}

            ${isLaravel ? card(isFilament ? t("filament_settings") : t("laravel_settings"), `
              ${spec(t("document_root"), `<code>public</code>`)}
              ${spec(t("database"), site.database ? `<code>${esc(site.database.database)}</code>` : "—")}
              ${laravelRetry ? spec(t("retry_setup") || "Retry Setup", laravelRetry) : ""}
              ${filamentRetry ? spec(t("retry_setup") || "Retry Setup", filamentRetry) : ""}
            `) : ""}
          </div>
        </div>

        <!-- TAB 3: DATABASE -->
        <div class="tabs-panel ${currentTab === "database" ? "is-active" : ""}" data-tab-panel="database">
          <div class="php-cards-grid">
            ${site.database ? card(t("database"), `
              ${spec(t("database_name"), `<code>${esc(site.database.database)}</code>`)}
              ${spec(t("admin_user"), `<code>${esc(site.database.username)}</code>`)}
              ${spec(t("host"), `<code>${esc(site.database.host)}:${esc(site.database.port)}</code>`)}
              ${spec(t("status"), badge(site.database.status))}
              ${spec(t("actions") || "Actions", `
                <div class="d-flex align-center gap-sm" style="flex-wrap:wrap;">
                  ${btn("db-reveal", t("reveal_credentials"))}
                  ${btn("db-rotate", t("rotate_password"))}
                  ${can("delete_database") ? btn("db-delete", t("delete_database"), "danger") : ""}
                </div>
              `)}
              <div class="php-detail__credentials" data-credentials hidden style="margin-top: 12px;"></div>
            `) : card(t("database"), `
              <p class="form-hint" style="margin:0;">${esc(t("no_database_attached"))}</p>
              ${can("create_database") ? `
                <div class="d-flex align-center gap-md" style="flex-wrap:wrap; margin-top: 12px;">
                  <label class="form-check" style="margin:0;"><input type="checkbox" data-db-install checked><span>${t("install_missing_extensions")}</span></label>
                  ${btn("db-create", t("create_database_for_site"), "primary")}
                </div>
              ` : ""}
            `)}
          </div>
        </div>

        <!-- TAB 4: SSL CERTIFICATES -->
        <div class="tabs-panel ${currentTab === "ssl" ? "is-active" : ""}" data-tab-panel="ssl">
          <div class="php-cards-grid">
            ${card(t("ssl_certificates"), `
              ${spec(t("status"), badge(ssl.active ? "active" : "inactive"))}
              ${spec(t("expires"), esc(sslExp || t("not_available")))}
              ${spec(t("include_www") || "Include WWW", `
                <label class="form-check" style="margin:0;"><input type="checkbox" data-ssl-www ${ssl.include_www ? "checked" : ""}><span>${esc(t("include_www"))}</span></label>
              `)}
              ${spec(t("actions") || "Actions", `
                <div class="d-flex align-center gap-sm" style="flex-wrap:wrap;">
                  ${sslAct}
                </div>
              `)}
            `)}
          </div>
        </div>

        <!-- TAB 5: LOGS -->
        <div class="tabs-panel ${currentTab === "logs" ? "is-active" : ""}" data-tab-panel="logs">
          <div class="php-cards-grid">
            <div class="php-card">
              <div class="php-card__title php-log-header">
                <span>${esc(t("logs"))}</span>
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
              <div style="padding-top: 4px;">
                <pre class="php-log-terminal" data-log-terminal style="margin:0;">${esc(t("loading"))}</pre>
              </div>
            </div>
          </div>
        </div>

        <!-- TAB 6: DANGER ZONE -->
        <div class="tabs-panel ${currentTab === "danger" ? "is-active" : ""}" data-tab-panel="danger">
          <div class="php-cards-grid">
            ${card(t("danger_zone"), `
              <p class="form-hint" style="max-width:640px; line-height:1.6; margin:0;">${esc(t("delete_php_site_desc"))}</p>
              <div class="d-flex align-center gap-md" style="flex-wrap:wrap; margin-top: 12px;">
                ${can("archive") ? btn("archive-site", t("archive_website"), "secondary") : ""}
                ${can("delete_site") ? btn("delete-site", t("delete_website"), "danger") : ""}
              </div>
            `, "php-card--danger")}
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
  if (action === "wp-cache-clear") {
    await request(actionUrl(id, "/wordpress/cache/clear"), "POST");
    await load();
    return;
  }
  if (action === "laravel-retry") {
    payload = await request(actionUrl(id, "/laravel/retry"), "POST", { install_missing_extensions: Boolean(content.querySelector("[data-laravel-install]")?.checked) });
  }
  if (action === "filament-retry") {
    const admin_name = content.querySelector("[data-filament-name]")?.value.trim();
    const admin_email = content.querySelector("[data-filament-email]")?.value.trim();
    const admin_password = content.querySelector("[data-filament-password]")?.value || "";
    if (!admin_name || !admin_email || admin_password.length < 12) throw new Error(t("filament_admin_required"));
    payload = await request(actionUrl(id, "/filament/retry"), "POST", {
      admin_name, admin_email, admin_password,
      install_missing_extensions: Boolean(content.querySelector("[data-filament-install]")?.checked),
    });
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
