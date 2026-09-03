/**
 * bulk-actions.js — Reusable Controller for Table Bulk Actions
 * Complies with .agents/rules/bulk_actions.md
 */

(function () {
  'use strict';

  function initBulkActionsTable(config) {
    const tableId = config.tableId;
    const endpoint = config.endpoint;
    const selectAllId = config.selectAllId || 'bulk-select-all';
    const itemSelector = config.itemSelector || '.bulk-select-item';
    const selectActionId = config.selectActionId || 'bulk-action-select';
    const applyBtnId = config.applyBtnId || 'bulk-action-apply-btn';
    const counterId = config.counterId || 'bulk-selected-count';
    const confirmDangerActions = config.confirmDangerActions || ['delete', 'uninstall', 'revoke'];
    const itemName = config.itemName || 'Item';
    const onSuccess = config.onSuccess || (() => window.location.reload());
    const getItemId = config.getItemId || (cb => {
      const num = parseInt(cb.value, 10);
      return isNaN(num) ? cb.value : num;
    });

    const table = document.getElementById(tableId);
    if (!table) return null;

    const selectAll = document.getElementById(selectAllId);
    const bulkSelect = document.getElementById(selectActionId);
    const bulkBtn = document.getElementById(applyBtnId);
    const counterEl = document.getElementById(counterId);

    function getItemCheckboxes() {
      return Array.from(table.querySelectorAll(itemSelector));
    }

    function getVisibleItemCheckboxes() {
      return getItemCheckboxes().filter(cb => {
        const row = cb.closest('tr');
        return !row || (row.style.display !== 'none' && !row.hidden);
      });
    }

    function updateState() {
      const visible = getVisibleItemCheckboxes();
      const checked = visible.filter(cb => cb.checked);
      const anyChecked = checked.length > 0;

      if (bulkSelect) {
        bulkSelect.disabled = !anyChecked;
      }
      if (bulkBtn) {
        bulkBtn.disabled = !anyChecked || !bulkSelect || !bulkSelect.value;
      }
      if (counterEl) {
        if (anyChecked) {
          counterEl.textContent = `(${checked.length})`;
          counterEl.style.display = '';
        } else {
          counterEl.textContent = '';
          counterEl.style.display = 'none';
        }
      }

      if (selectAll) {
        selectAll.checked = visible.length > 0 && checked.length === visible.length;
        selectAll.indeterminate = checked.length > 0 && checked.length < visible.length;
      }
    }

    if (selectAll) {
      selectAll.addEventListener('change', (e) => {
        getVisibleItemCheckboxes().forEach(cb => {
          cb.checked = e.target.checked;
        });
        updateState();
      });
    }

    table.addEventListener('change', (e) => {
      if (e.target && e.target.matches(itemSelector)) {
        updateState();
      }
    });

    if (bulkSelect) {
      bulkSelect.addEventListener('change', updateState);
    }

    if (bulkBtn) {
      bulkBtn.addEventListener('click', () => {
        const action = bulkSelect ? bulkSelect.value : '';
        const checkedBoxes = getVisibleItemCheckboxes().filter(cb => cb.checked);
        const selectedIds = checkedBoxes.map(getItemId);

        if (!action || selectedIds.length === 0) return;

        const isDanger = confirmDangerActions.includes(action.toLowerCase());
        const selectedLabel = `${selectedIds.length} ${itemName}${selectedIds.length > 1 ? 's' : ''}`;
        const confirmMsg = isDanger
          ? `Are you sure you want to permanently ${action} ${selectedLabel}? This action cannot be undone.`
          : `Apply '${action}' to ${selectedLabel}?`;

        const execute = async () => {
          bulkBtn.disabled = true;
          const origText = bulkBtn.textContent;
          bulkBtn.textContent = '...';

          try {
            const csrfToken =
              document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ||
              document.querySelector('[name="csrf_token"]')?.value ||
              '';

            const res = await fetch(endpoint, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken
              },
              body: JSON.stringify({ action: action, item_ids: selectedIds, ids: selectedIds })
            });

            const data = await res.json().catch(() => ({}));

            if (res.ok && (data.success !== false)) {
              if (typeof window.toast === 'function') {
                window.toast(data.message || `Successfully executed ${action} on ${selectedLabel}.`, 'success');
              }
              onSuccess(data);
            } else {
              const errMsg = data.detail || data.message || `Failed to execute ${action}.`;
              if (typeof window.toast === 'function') {
                window.toast(errMsg, 'danger');
              } else {
                alert(errMsg);
              }
              bulkBtn.disabled = false;
              bulkBtn.textContent = origText;
            }
          } catch (err) {
            const msg = err.message || 'Operation failed';
            if (typeof window.toast === 'function') {
              window.toast(msg, 'danger');
            } else {
              alert(msg);
            }
            bulkBtn.disabled = false;
            bulkBtn.textContent = origText;
          }
        };

        if (typeof window.confirmAction === 'function') {
          window.confirmAction(confirmMsg, execute, {
            danger: isDanger,
            title: `Bulk ${action.charAt(0).toUpperCase() + action.slice(1)}`,
            okLabel: isDanger ? `${action.charAt(0).toUpperCase() + action.slice(1)} ${selectedIds.length}` : 'Apply',
            itemName: selectedLabel
          });
        } else if (confirm(confirmMsg)) {
          execute();
        }
      });
    }

    // Initial state calculation
    updateState();

    return {
      updateState,
      getSelectedIds: () => getVisibleItemCheckboxes().filter(cb => cb.checked).map(getItemId)
    };
  }

  window.initBulkActionsTable = initBulkActionsTable;
})();
