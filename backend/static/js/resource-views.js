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
    const wrap = container.nextElementSibling;
    if (wrap && wrap.classList.contains('load-more-wrap')) {
      wrap.style.display = isVisible ? 'flex' : 'none';
    }
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

function toggleListRowActions(triggerEl) {
  const row = triggerEl.closest('.list-item-row, tr');
  if (!row) return;

  const subactions = row.querySelector('.list-row-subactions');
  if (!subactions) return;

  const toggleBtn = row.querySelector('.list-actions-toggle-btn');
  const isExpanded = row.classList.contains('is-expanded');
  const willExpand = !isExpanded;

  const container = row.closest('.view-list-container, tbody, .table-wrap, .plugin-grid, .dependency-grid') || row.parentElement;
  if (container) {
    container.querySelectorAll('.is-expanded').forEach((otherRow) => {
      if (otherRow !== row) {
        otherRow.classList.remove('is-expanded');
        otherRow.classList.remove('is-actions-open');
        const tray = otherRow.querySelector('.list-row-subactions');
        if (tray) tray.classList.add('is-hidden');
        const btn = otherRow.querySelector('.list-actions-toggle-btn');
        if (btn) {
          btn.classList.remove('is-active');
          btn.setAttribute('aria-expanded', 'false');
        }
      }
    });
  }

  subactions.classList.toggle('is-hidden', !willExpand);
  row.classList.toggle('is-expanded', willExpand);
  row.classList.toggle('is-actions-open', willExpand);
  
  if (toggleBtn) {
    toggleBtn.classList.toggle('is-active', willExpand);
    toggleBtn.setAttribute('aria-expanded', String(willExpand));
  }
}
window.toggleListRowActions = toggleListRowActions;

// Hover synchronization for table rows
document.addEventListener('mouseover', (e) => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  tr.classList.add('is-hovered');
});

document.addEventListener('mouseout', (e) => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  tr.classList.remove('is-hovered');
});

/**
 * Base Client-Side List Pagination ("Load More" button engine).
 * Configured with default limit of 6 items per page.
 */
const DEFAULT_PAGE_LIMIT = 6;

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

  // Bind single load-more-wrap per specific container view
  const viewId = container.id || container.dataset.resourceView || 'default';
  const parent = container.parentElement;
  let wrap = parent ? parent.querySelector(`.load-more-wrap[data-for-view="${viewId}"]`) : null;

  if (!wrap) {
    // Check direct sibling
    const sibling = container.nextElementSibling;
    if (sibling && sibling.classList.contains('load-more-wrap')) {
      wrap = sibling;
      wrap.dataset.forView = viewId;
    }
  }

  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'load-more-wrap';
    wrap.dataset.forView = viewId;
    container.after(wrap);
  }

  const isContainerHidden = container.classList.contains('is-hidden') || (parent && parent.classList.contains('is-hidden'));

  if (remainingCount > 0 && !isContainerHidden) {
    wrap.style.display = 'flex';
    wrap.innerHTML = `
      <button type="button" class="btn btn--secondary btn--sm load-more-btn">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
        <span>Load More</span>
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

document.addEventListener('app:init', () => {
  // Add slide-toolbar class to all subactions
  document.querySelectorAll('.list-row-subactions').forEach(div => {
    div.classList.add('slide-toolbar');
  });

  autoInitAllListPaginations(DEFAULT_PAGE_LIMIT);
});

