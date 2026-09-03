/**
 * bulk-actions.js — Shared Declarative Controller for Table Bulk Actions
 * Auto-initializes any table with data-bulk-endpoint or .action-toolbar[data-bulk-endpoint]
 */

(function () {
  'use strict';

  function initBulkActionsTable(config) {
    const tableEl = typeof config.table === 'string' ? document.getElementById(config.table) : (config.table || document.querySelector(config.tableSelector || 'table'));
    if (!tableEl) return null;

    const toolbar = config.toolbar || tableEl.closest('.content__inner, .apps-page, main')?.querySelector('.action-toolbar') || document.querySelector('.action-toolbar');
    const endpoint = config.endpoint || toolbar?.getAttribute('data-bulk-endpoint') || tableEl.getAttribute('data-bulk-endpoint');
    if (!endpoint) return null;

    const selectAll = toolbar?.querySelector('#bulk-select-all') || tableEl.querySelector('#bulk-select-all');
    const bulkSelect = toolbar?.querySelector('#bulk-action-select');
    const bulkBtn = toolbar?.querySelector('#bulk-action-apply-btn');
    const counterEl = toolbar?.querySelector('#bulk-selected-count');
    const searchInput = toolbar?.querySelector('.action-toolbar__search input');
    const statusFilter = toolbar?.querySelector('#status-filter-select');
    const itemName = config.itemName || toolbar?.getAttribute('data-bulk-item-name') || 'Item';
    const confirmDanger = (config.confirmDangerActions || ['delete', 'uninstall', 'revoke']).map(a => a.toLowerCase());

    function getVisibleItemBoxes() {
      return Array.from(tableEl.querySelectorAll('.bulk-select-item')).filter(cb => {
        const row = cb.closest('tr');
        return !row || (row.style.display !== 'none' && !row.hidden);
      });
    }

    function updateState() {
      const visible = getVisibleItemBoxes();
      const checked = visible.filter(cb => cb.checked);
      const hasChecked = checked.length > 0;

      if (bulkSelect) bulkSelect.disabled = !hasChecked;
      if (bulkBtn) bulkBtn.disabled = !hasChecked || !bulkSelect || !bulkSelect.value;
      if (counterEl) {
        counterEl.textContent = hasChecked ? `(${checked.length})` : '';
        counterEl.style.display = hasChecked ? '' : 'none';
      }
      if (selectAll) {
        selectAll.checked = visible.length > 0 && checked.length === visible.length;
        selectAll.indeterminate = checked.length > 0 && checked.length < visible.length;
      }
    }

    function filterTable() {
      const q = (searchInput?.value || '').toLowerCase().trim();
      const filter = (statusFilter?.value || 'all').toLowerCase();
      const rows = tableEl.querySelectorAll('tbody tr');

      rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        const matchesQ = !q || text.includes(q);
        const matchesF = filter === 'all' || text.includes(filter);

        if (matchesQ && matchesF) {
          row.style.display = '';
        } else {
          row.style.display = 'none';
          const cb = row.querySelector('.bulk-select-item');
          if (cb) cb.checked = false;
        }
      });
      updateState();
    }

    if (selectAll) {
      selectAll.addEventListener('change', (e) => {
        getVisibleItemBoxes().forEach(cb => { cb.checked = e.target.checked; });
        updateState();
      });
    }

    tableEl.addEventListener('change', (e) => {
      if (e.target?.matches('.bulk-select-item')) updateState();
    });

    if (bulkSelect) bulkSelect.addEventListener('change', updateState);
    if (searchInput) searchInput.addEventListener('input', filterTable);
    if (statusFilter) statusFilter.addEventListener('change', filterTable);

    if (bulkBtn) {
      bulkBtn.addEventListener('click', () => {
        const action = bulkSelect?.value || '';
        const checked = getVisibleItemBoxes().filter(cb => cb.checked);
        const ids = checked.map(cb => {
          const num = parseInt(cb.value, 10);
          return isNaN(num) ? cb.value : num;
        });

        if (!action || !ids.length) return;

        const isDanger = confirmDanger.includes(action.toLowerCase());
        const label = `${ids.length} ${itemName}${ids.length > 1 ? 's' : ''}`;
        const confirmMsg = isDanger
          ? `Permanently ${action} ${label}? This cannot be undone.`
          : `Apply '${action}' to ${label}?`;

        const run = async () => {
          bulkBtn.disabled = true;
          const prev = bulkBtn.textContent;
          bulkBtn.textContent = '...';

          try {
            const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ||
                         document.querySelector('[name="csrf_token"]')?.value || '';

            const res = await fetch(endpoint, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
              body: JSON.stringify({ action, item_ids: ids, ids })
            });

            const data = await res.json().catch(() => ({}));
            if (res.ok && data.success !== false) {
              if (typeof window.toast === 'function') window.toast(data.message || `Done: ${action} on ${label}`, 'success');
              window.location.reload();
            } else {
              const msg = data.detail || data.message || `Failed to ${action}.`;
              if (typeof window.toast === 'function') window.toast(msg, 'danger');
              else alert(msg);
              bulkBtn.disabled = false;
              bulkBtn.textContent = prev;
            }
          } catch (err) {
            if (typeof window.toast === 'function') window.toast(err.message || 'Action failed', 'danger');
            else alert(err.message);
            bulkBtn.disabled = false;
            bulkBtn.textContent = prev;
          }
        };

        if (typeof window.confirmAction === 'function') {
          window.confirmAction(confirmMsg, run, {
            danger: isDanger,
            title: `Bulk ${action.charAt(0).toUpperCase() + action.slice(1)}`,
            okLabel: isDanger ? `${action.charAt(0).toUpperCase() + action.slice(1)} ${ids.length}` : 'Apply',
            itemName: label
          });
        } else if (confirm(confirmMsg)) {
          run();
        }
      });
    }

    updateState();
    return { updateState, filterTable };
  }

  // Auto-bind on DOM ready
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.action-toolbar[data-bulk-endpoint]').forEach(tb => {
      const targetId = tb.getAttribute('data-bulk-table');
      const table = targetId ? document.getElementById(targetId) : tb.closest('.content__inner, main')?.querySelector('table');
      if (table) {
        initBulkActionsTable({ table, toolbar: tb });
      }
    });
  });

  window.initBulkActionsTable = initBulkActionsTable;
})();
