/** Shared Cards / Compact / List view switching for resource pages. */
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-resource-view]");
  if (!button) return;

  const switcher = button.closest("[data-resource-switcher]");
  if (!switcher) return;

  const resourceId = switcher.dataset.resourceSwitcher;
  const view = button.dataset.resourceView;
  document.querySelectorAll(`[data-resource-switcher="${resourceId}"] [data-resource-view]`).forEach((item) => {
    const active = item === button;
    item.classList.toggle("is-active", active);
    item.setAttribute("aria-checked", String(active));
  });
  document.querySelectorAll(`[data-resource-container="${resourceId}"]`).forEach((container) => {
    container.classList.toggle("is-hidden", container.dataset.resourceView !== view);
  });
});

document.addEventListener("error", (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement) || !image.matches("[data-fallback-image]")) return;
  image.hidden = true;
  image.nextElementSibling?.classList.remove("is-hidden");
}, true);

/**
 * Toggle list record actions sub-tray under the row.
 * @param {HTMLElement} triggerEl
 */
function toggleListRowActions(triggerEl) {
  const row = triggerEl.closest('.list-item-row') || triggerEl.closest('.item-card') || triggerEl.closest('.plugin-requirements-host');
  if (!row) return;

  const subactions = row.querySelector('.list-row-subactions');
  if (!subactions) return;

  const isExpanded = triggerEl.getAttribute('aria-expanded') === 'true';
  const willExpand = !isExpanded;

  // Close any other open action sub-trays in the list container for clean UI
  const container = row.closest('.view-list-container') || row.parentElement;
  if (container) {
    container.querySelectorAll('.list-row-subactions').forEach((tray) => {
      if (tray !== subactions) {
        tray.classList.add('is-hidden');
        const parentRow = tray.closest('.list-item-row') || tray.closest('.plugin-requirements-host');
        const btn = parentRow?.querySelector('.list-actions-toggle-btn');
        if (btn) {
          btn.classList.remove('is-active');
          btn.setAttribute('aria-expanded', 'false');
        }
      }
    });
  }

  subactions.classList.toggle('is-hidden', !willExpand);
  triggerEl.classList.toggle('is-active', willExpand);
  triggerEl.setAttribute('aria-expanded', String(willExpand));
}
window.toggleListRowActions = toggleListRowActions;

