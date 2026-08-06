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
 * Toggle list record actions sub-tray under the row (supports div rows and table <tr> rows).
 * @param {HTMLElement} triggerEl
 */
function toggleListRowActions(triggerEl) {
  // 1. Table row handling
  const tr = triggerEl.closest('tr');
  if (tr && !tr.classList.contains('table-row-subactions-tr')) {
    const nextTr = tr.nextElementSibling;
    const subactionsTr = (nextTr && nextTr.classList.contains('table-row-subactions-tr')) ? nextTr : null;
    if (!subactionsTr) return;

    const isExpanded = triggerEl.getAttribute('aria-expanded') === 'true';
    const willExpand = !isExpanded;

    // Close other open subaction rows in the same table
    const tbody = tr.closest('tbody') || tr.parentElement;
    if (tbody) {
      tbody.querySelectorAll('.table-row-subactions-tr:not(.is-hidden)').forEach((otherTr) => {
        if (otherTr !== subactionsTr) {
          otherTr.classList.add('is-hidden');
          const prevTr = otherTr.previousElementSibling;
          if (prevTr) prevTr.classList.remove('is-expanded');
          const btn = prevTr?.querySelector('.list-actions-toggle-btn');
          if (btn) {
            btn.classList.remove('is-active');
            btn.setAttribute('aria-expanded', 'false');
          }
        }
      });
    }

    subactionsTr.classList.toggle('is-hidden', !willExpand);
    tr.classList.toggle('is-expanded', willExpand);
    triggerEl.classList.toggle('is-active', willExpand);
    triggerEl.setAttribute('aria-expanded', String(willExpand));
    return;
  }

  // 2. Div-based list row handling
  const row = triggerEl.closest('.list-item-row') || triggerEl.closest('.item-card') || triggerEl.closest('.plugin-requirements-host');
  if (!row) return;

  const subactions = row.querySelector('.list-row-subactions');
  if (!subactions) return;

  const isExpanded = triggerEl.getAttribute('aria-expanded') === 'true';
  const willExpand = !isExpanded;

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

// Hover synchronization for table rows and their expanded subactions
document.addEventListener('mouseover', (e) => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  
  if (tr.classList.contains('table-row-subactions-tr')) {
    tr.classList.add('is-hovered');
    const prev = tr.previousElementSibling;
    if (prev) prev.classList.add('is-hovered');
  } else {
    tr.classList.add('is-hovered');
    const next = tr.nextElementSibling;
    if (next && next.classList.contains('table-row-subactions-tr') && !next.classList.contains('is-hidden')) {
      next.classList.add('is-hovered');
    }
  }
});

document.addEventListener('mouseout', (e) => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  
  if (tr.classList.contains('table-row-subactions-tr')) {
    tr.classList.remove('is-hovered');
    const prev = tr.previousElementSibling;
    if (prev) prev.classList.remove('is-hovered');
  } else {
    tr.classList.remove('is-hovered');
    const next = tr.nextElementSibling;
    if (next && next.classList.contains('table-row-subactions-tr')) {
      next.classList.remove('is-hovered');
    }
  }
});

