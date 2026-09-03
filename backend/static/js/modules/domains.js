/**
 * modules/domains.js — Domain list and management logic
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    if (typeof hideSkeleton === 'function') hideSkeleton('domains-skeleton');
    else if (typeof window.hideSkeleton === 'function') window.hideSkeleton('domains-skeleton');
  });

  window.deleteDomain = function (id, name) {
    if (typeof window.confirmAction !== 'function') return;

    window.confirmAction(
      `Delete domain "${name}"? This will remove the DNS zone, Nginx config, webroot, and linked SSL certificate.`,
      async () => {
        if (typeof window.openTaskDrawer === 'function') window.openTaskDrawer('auto');
        try {
          const csrfToken = typeof getCsrfToken === 'function' ? getCsrfToken() : '';
          const response = await fetch(`/domains/${id}/delete`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded',
              'X-Requested-With': 'XMLHttpRequest',
              'Accept': 'application/json',
            },
            body: new URLSearchParams({ csrf_token: csrfToken }),
          });
          const data = await response.json();
          if (response.ok && (data.success || data.status === 'running')) {
            if (typeof window.toast === 'function') window.toast(`Domain "${name}" deleted successfully.`, 'success');
            const row = document.querySelector(`tr[data-domain-id="${id}"]`);
            if (row) {
              row.style.transition = 'opacity 0.25s ease';
              row.style.opacity = '0';
              setTimeout(() => row.remove(), 250);
            }
            if (typeof window.refreshTasks === 'function') window.refreshTasks();
          } else {
            if (typeof window.toast === 'function') window.toast(data.error || data.message || 'Failed to delete domain', 'danger');
          }
        } catch (e) {
          if (typeof window.toast === 'function') window.toast(e.message || 'Network error while deleting domain', 'danger');
        }
      },
      { danger: true, title: 'Delete Domain', itemName: name, okLabel: 'Delete Domain' }
    );
  };
})();
