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
    const isVisible = container.dataset.resourceView === view;
    container.classList.toggle("is-hidden", !isVisible);
    if (isVisible && typeof applyListPagination === 'function') {
      applyListPagination(container);
    }
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

/**
 * Base Client-Side List Pagination ("Load More" button engine).
 * Configured with test limit of 3 items.
 */
const DEFAULT_PAGE_LIMIT = 3;

function applyListPagination(container, limit = DEFAULT_PAGE_LIMIT) {
  if (!container) return;

  let itemRows = [];
  const tbody = container.querySelector('tbody');
  if (tbody) {
    itemRows = Array.from(tbody.children).filter(
      (el) => el.tagName === 'TR' && !el.classList.contains('table-row-subactions-tr') && !el.classList.contains('cache-settings-row')
    );
  } else {
    itemRows = Array.from(container.children).filter(
      (el) => (el.classList.contains('list-item-row') || el.classList.contains('plugin-card') || el.classList.contains('dependency-card') || el.classList.contains('compact-card-1x1') || el.classList.contains('item-card')) && !el.classList.contains('skeleton-overlay')
    );
  }

  if (!itemRows.length) return;

  const visibleMatching = itemRows.filter(
    (r) => r.getAttribute('data-search-hidden') !== 'true' && (r.style.display !== 'none' || r.classList.contains('is-page-hidden'))
  );

  let currentLimit = parseInt(container.dataset.visibleLimit || String(limit), 10);
  container.dataset.visibleLimit = String(currentLimit);

  visibleMatching.forEach((row, index) => {
    const nextSub = row.nextElementSibling && row.nextElementSibling.classList.contains('table-row-subactions-tr') ? row.nextElementSibling : null;
    if (index < currentLimit) {
      row.classList.remove('is-page-hidden');
    } else {
      row.classList.add('is-page-hidden');
      if (nextSub) {
        nextSub.classList.add('is-hidden');
        nextSub.classList.add('is-page-hidden');
      }
    }
  });

  const remainingCount = Math.max(0, visibleMatching.length - currentLimit);

  // Parent wrapper or next sibling
  let wrap = container.nextElementSibling;
  if (!wrap || !wrap.classList.contains('load-more-wrap')) {
    const parent = container.closest('.table-wrap') || container.parentElement;
    if (parent) wrap = parent.querySelector('.load-more-wrap');
  }

  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'load-more-wrap';
    const targetParent = container.closest('.table-wrap') || container.parentElement;
    targetParent.after(wrap);
  }

  if (remainingCount > 0) {
    wrap.style.display = 'flex';
    wrap.innerHTML = `
      <button type="button" class="btn btn--secondary btn--sm load-more-btn">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
        <span>Load More (${remainingCount} remaining)</span>
      </button>
    `;
    const btn = wrap.querySelector('.load-more-btn');
    btn.onclick = () => {
      container.dataset.visibleLimit = String(currentLimit + limit);
      applyListPagination(container, limit);
    };
  } else {
    wrap.style.display = 'none';
  }
}
window.applyListPagination = applyListPagination;

function autoInitAllListPaginations(limit = DEFAULT_PAGE_LIMIT) {
  const containers = document.querySelectorAll(
    '[data-resource-container], .table-wrap, .view-list-container, .plugin-grid, .plugin-compact-grid, .dependency-grid, .dependency-compact-grid'
  );
  containers.forEach((c) => {
    if (c.classList.contains('table-wrap') && c.querySelector('table')) {
      applyListPagination(c.querySelector('table'), limit);
    } else {
      applyListPagination(c, limit);
    }
  });
}
window.autoInitAllListPaginations = autoInitAllListPaginations;

document.addEventListener('DOMContentLoaded', () => {
  autoInitAllListPaginations(DEFAULT_PAGE_LIMIT);
});

