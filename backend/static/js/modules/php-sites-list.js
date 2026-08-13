import { esc, request, statusTone, t } from "./php-sites-api.js";

const root = document.querySelector("[data-php-sites-list]");
const table = root?.querySelector("[data-site-table]");
const empty = root?.querySelector("[data-site-empty]");
const error = root?.querySelector("[data-site-error]");
const count = root?.querySelector("[data-site-count]");

function statusBadge(status) {
  return `<span class="status-badge status-badge--${statusTone(status)}">${esc(status || "unknown")}</span>`;
}

function renderSites(sites) {
  if (count) count.textContent = `${sites.length} ${t("total")}`;
  if (!sites.length) {
    empty.hidden = false;
    table.hidden = true;
    return;
  }
  table.innerHTML = `<table class="table"><thead><tr><th>${t("website")}</th><th>${t("preset")}</th><th>${t("php_version")}</th><th>${t("status")}</th><th class="col-actions">${t("actions")}</th></tr></thead><tbody>${sites.map((site) => `<tr><td class="table-title"><a href="/php-sites/${site.id}">${esc(site.domain)}</a></td><td>${esc(site.preset === "wordpress" ? t("wordpress") : t("plain_php"))}</td><td><code>${esc(site.php_version)}</code></td><td>${statusBadge(site.status)}</td><td class="col-actions"><a class="icon-btn" href="/php-sites/${site.id}" title="${t("manage")}" aria-label="${t("manage")}"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"></path><path d="m19.4 15 .1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V19a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 6.8 18l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 2.8 12H3a2 2 0 1 1 0-4h-.1A1.7 1.7 0 0 0 4 5.1l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 1.2V1a2 2 0 1 1 4 0v.1A1.7 1.7 0 0 0 16.9 2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21.2 8h-.1a2 2 0 1 1 0 4h.1a1.7 1.7 0 0 0-1.8 3Z"></path></svg></a></td></tr>`).join("")}</tbody></table>`;
  table.hidden = false;
  empty.hidden = true;
}

async function load() {
  try {
    const payload = await request("/sites");
    renderSites(payload.sites || []);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    if (window.hideSkeleton) window.hideSkeleton("php-sites-list-skeleton");
  }
}

if (root) load();
