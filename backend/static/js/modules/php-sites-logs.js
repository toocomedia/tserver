import { esc, request, t } from "./php-sites-api.js";

export function renderLogSection() {
  return `
    <section class="info-section php-detail__section php-detail__section--wide">
      <div class="info-section-header php-detail__logs-header">
        <h3>${esc(t("logs"))}</h3>
        <div class="php-detail__logs-controls">
          <div class="php-log-tabs">
            <button type="button" class="btn btn--sm btn--secondary php-log-tab is-active" data-log-stream="access">${esc(t("access_log"))}</button>
            <button type="button" class="btn btn--sm btn--secondary php-log-tab" data-log-stream="nginx_error">${esc(t("nginx_error_log"))}</button>
            <button type="button" class="btn btn--sm btn--secondary php-log-tab" data-log-stream="php">${esc(t("php_fpm_log"))}</button>
          </div>
          <div class="php-log-actions">
            <select class="form-select php-log-lines" data-log-lines style="height:30px; font-size:12px; padding:0 8px;">
              <option value="50">50 ${esc(t("lines"))}</option>
              <option value="100" selected>100 ${esc(t("lines"))}</option>
              <option value="200">200 ${esc(t("lines"))}</option>
              <option value="500">500 ${esc(t("lines"))}</option>
            </select>
            <button type="button" class="btn btn--sm btn--secondary" data-log-refresh>${esc(t("refresh"))}</button>
          </div>
        </div>
      </div>
      <div class="php-detail__logs-body">
        <pre class="php-log-terminal" data-log-terminal>${esc(t("loading"))}</pre>
      </div>
    </section>
  `;
}

export async function loadLogs(siteId, container) {
  const terminal = container.querySelector("[data-log-terminal]");
  const activeTab = container.querySelector(".php-log-tab.is-active");
  const linesSelect = container.querySelector("[data-log-lines]");
  if (!terminal) return;

  const stream = activeTab?.dataset.logStream || "access";
  const lines = linesSelect?.value || "100";

  terminal.textContent = t("loading");
  try {
    const res = await request(`/sites/${encodeURIComponent(siteId)}/logs?stream=${encodeURIComponent(stream)}&lines=${encodeURIComponent(lines)}`);
    const content = (res.lines || []).join("\n");
    terminal.textContent = content.trim() || `[No ${stream} log entries recorded]`;
    terminal.scrollTop = terminal.scrollHeight;
  } catch (err) {
    terminal.textContent = `[Log fetch error: ${err.message}]`;
  }
}

export function bindLogEvents(siteId, container) {
  container.querySelectorAll(".php-log-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      container.querySelectorAll(".php-log-tab").forEach((t) => t.classList.remove("is-active", "btn--dark"));
      tab.classList.add("is-active", "btn--dark");
      loadLogs(siteId, container);
    });
  });
  container.querySelector("[data-log-lines]")?.addEventListener("change", () => loadLogs(siteId, container));
  container.querySelector("[data-log-refresh]")?.addEventListener("click", () => loadLogs(siteId, container));
}
