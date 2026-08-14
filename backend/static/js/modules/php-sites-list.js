import { esc, request, statusTone, t } from "./php-sites-api.js";

const root = document.querySelector("[data-php-sites-list]");
const container = root?.querySelector("[data-sites-container]");
const errorEl = root?.querySelector("[data-site-error]");

function renderTitleBar(count) {
  return `
    <div class="resource-title-bar mb-md">
      <div class="resource-title-bar__main">
        <div class="resource-title-bar__icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16v14H4z"></path><path d="M8 9h8M8 13h5"></path></svg>
        </div>
        <h2 class="resource-title-bar__title">${esc(t("managed_websites"))}</h2>
      </div>
      <div class="resource-title-bar__count">${count} ${esc(t("managed_websites"))}</div>
    </div>
  `;
}

function renderTable(sites) {
  const rows = sites.map((site) => {
    const tone = statusTone(site.status);
    const presetLabel = site.preset === "wordpress" ? t("wordpress") : site.preset === "laravel" ? t("laravel") : t("plain_php");
    const statusLabel = (site.status || "").replace(/_/g, " ");
    return `
      <tr data-site-id="${site.id}">
        <td class="table-title"><a href="/php-sites/${site.id}">${esc(site.domain || "—")}</a></td>
        <td>${esc(presetLabel)}</td>
        <td><code>${esc(site.php_version)}</code></td>
        <td><span class="status-badge status-badge--${tone}">${esc(statusLabel)}</span></td>
        <td class="col-actions" style="display:flex; gap:6px; justify-content:flex-end;">
          <a class="icon-btn" href="/php-sites/${site.id}" title="${esc(t("manage_php_site"))}" aria-label="${esc(t("manage_php_site"))}">
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"></path><path d="m19.4 15 .1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V19a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 6.8 18l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 2.8 12H3a2 2 0 1 1 0-4h-.1A1.7 1.7 0 0 0 4 5.1l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 1.2V1a2 2 0 1 1 4 0v.1A1.7 1.7 0 0 0 16.9 2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21.2 8h-.1a2 2 0 1 1 0 4h.1a2 2 0 1 1-1.8 3Z"></path></svg>
          </a>
        </td>
      </tr>
    `;
  }).join("");

  return `
    ${renderTitleBar(sites.length)}
    <div class="table-wrap">
      <table class="table php-sites-table">
        <colgroup><col class="php-sites-table__domain"><col><col><col><col class="php-sites-table__actions"></colgroup>
        <thead>
          <tr>
            <th>${esc(t("website"))}</th>
            <th>${esc(t("preset"))}</th>
            <th>${esc(t("php_version"))}</th>
            <th>${esc(t("status"))}</th>
            <th class="col-actions">${esc(t("actions"))}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderEmptyState() {
  return `
    <div class="empty-state-strict">
      <div class="empty-state-shapes">
        <div class="shape-2"></div>
        <div class="shape-1"></div>
        <div class="shape-3"></div>
      </div>
      <h3 class="empty-state-strict__title">${esc(t("no_php_websites"))}</h3>
      <p class="empty-state-strict__desc">${esc(t("choose_php_wordpress_or_laravel_desc"))}</p>
      <div class="empty-state__actions mt-lg" style="display:flex; justify-content:center;">
        <a class="btn btn--primary" href="/php-sites/create" style="padding: 0 24px; height: 40px; font-weight: 600; display: inline-flex; align-items: center;">+ ${esc(t("create_php_site"))}</a>
      </div>
    </div>
  `;
}

async function loadSites() {
  try {
    const res = await request("/sites");
    const sites = res.sites || [];
    container.innerHTML = sites.length > 0 ? renderTable(sites) : renderEmptyState();
    container.style.display = "block";
    if (typeof window.hideSkeleton === "function") {
      window.hideSkeleton("php-sites-list-skeleton", 0);
    }
  } catch (err) {
    if (typeof window.hideSkeleton === "function") {
      window.hideSkeleton("php-sites-list-skeleton", 0);
    }
    if (errorEl) {
      errorEl.textContent = err.message;
      errorEl.hidden = false;
    }
  }
}

if (root) loadSites();
