/**
 * updates.js — Light Git update check & silent background deployment.
 */
document.addEventListener('app:init', () => {
  const btnCheck = document.getElementById('btn-check-updates');
  const btnCheckText = document.getElementById('btn-check-updates-text');
  const btnApply = document.getElementById('btn-apply-update');
  const chkAutoUpdate = document.getElementById('chk_auto_update');
  
  const elLocalCommit = document.getElementById('update-local-commit');
  const elRemoteCommit = document.getElementById('update-remote-commit');
  const elCommitMsg = document.getElementById('update-commit-msg');
  const elLastChecked = document.getElementById('update-last-checked');
  const elStatusBadge = document.getElementById('update-status-badge');
  const updateSkeleton = document.querySelector('[data-update-skeleton]');

  if (!btnCheck) return; // Not on settings page

  async function checkUpdates(force = false) {
    if (btnCheckText) btnCheckText.textContent = window._('js.checking');
    if (btnCheck) btnCheck.disabled = true;

    try {
      const url = force ? '/api/updates/check?force=true' : '/api/updates/check';
      const data = await window.panel.get(url);

      // Render info
      if (elLocalCommit) elLocalCommit.innerHTML = `<code>${data.local_short_sha || 'unknown'}</code>`;
      if (elRemoteCommit) elRemoteCommit.innerHTML = `<code>${data.remote_short_sha || 'unknown'}</code>`;
      if (elCommitMsg) elCommitMsg.textContent = data.commit_message || '—';
      if (elLastChecked) elLastChecked.textContent = data.last_checked || window._('js.just_now');
      if (chkAutoUpdate && typeof data.auto_update_enabled === 'boolean') {
        chkAutoUpdate.checked = data.auto_update_enabled;
      }

      // Status badge
      if (data.has_update) {
        if (elStatusBadge) {
          elStatusBadge.className = 'badge badge--error badge--dot';
          elStatusBadge.textContent = window._('update_available');
        }
        if (btnApply) {
          btnApply.disabled = false;
          btnApply.textContent = window._('js.update_and_restart');
        }
      } else {
        if (elStatusBadge) {
          elStatusBadge.className = 'badge badge--ok badge--dot';
          elStatusBadge.textContent = window._('up_to_date');
        }
        if (btnApply) {
          btnApply.disabled = true;
          btnApply.textContent = window._('js.already_up_to_date');
        }
      }
      updateSkeleton?.classList.remove('is-data-loading');
      updateSkeleton?.setAttribute('aria-busy', 'false');
    } catch (err) {
      console.error('Update check failed:', err);
      if (elStatusBadge) {
        elStatusBadge.className = 'badge badge--error';
        elStatusBadge.textContent = window._('js.check_failed');
      }
      updateSkeleton?.classList.remove('is-data-loading');
      updateSkeleton?.setAttribute('aria-busy', 'false');
      if (elStatusMsg) elStatusMsg.textContent = `${window._('js.check_failed')}: ${err.message}`;
    } finally {
      if (btnCheckText) btnCheckText.textContent = window._('check_for_updates');
      if (btnCheck) btnCheck.disabled = false;
    }
  }

  async function toggleAutoUpdate(enabled) {
    try {
      await window.panel.post('/api/updates/auto-update', { enabled });
      if (typeof window.toast === 'function') {
        window.toast(window._('js.automatic_updates_status').replace('{status}', enabled ? 'enabled' : 'disabled'), 'success');
      }
    } catch (err) {
      console.error('Failed to update auto-update setting:', err);
      if (typeof window.toast === 'function') {
        window.toast(window._('js.could_not_save_autoupdate').replace('{error}', err.message), 'danger');
      }
      if (chkAutoUpdate) chkAutoUpdate.checked = !enabled;
    }
  }

  async function applyUpdate() {
    if (!confirm(window._('js.confirm_update_and_restart'))) {
      return;
    }

    if (btnApply) {
      btnApply.disabled = true;
      btnApply.innerHTML = `<span class="spinner" style="display:inline-block; border:2px solid currentColor; border-top-color:transparent; border-radius:50%; width:12px; height:12px; animation:spin 1s linear infinite; margin-right:6px;"></span> ${window._('js.updating_in_background')}`;
    }

    try {
      const data = await window.panel.post('/api/updates/apply', {});
      if (data.status === 'error') {
        if (typeof window.toast === 'function') {
          window.toast(data.message, 'danger');
        }
        if (btnApply) {
          btnApply.disabled = false;
          btnApply.textContent = window._('js.update_and_restart');
        }
        return;
      }

      if (typeof window.toast === 'function') {
        window.toast(window._('js.update_started_in_background'), 'success');
      }

      // Start background reconnect polling
      startPollingHealth();
    } catch (err) {
      console.error('Apply update trigger:', err);
      if (typeof window.toast === 'function') {
        window.toast(window._('js.update_process_launched'), 'info');
      }
      startPollingHealth();
    }
  }

  function startPollingHealth() {
    let sawRestart = false;
    let healthyChecksAfterRestart = 0;

    const healthInterval = setInterval(async () => {
      try {
        const res = await fetch('/api/health', { cache: 'no-store' });
        if (!res.ok) {
          sawRestart = true;
          healthyChecksAfterRestart = 0;
          if (btnApply) {
            btnApply.textContent = window._('js.panel_restarting_waiting');
          }
          return;
        }

        // The first healthy response can be from the old panel, before its
        // scheduled restart begins. Reload only after the outage and two
        // confirmed healthy responses from the restarted panel.
        if (sawRestart) {
          healthyChecksAfterRestart += 1;
        }
        if (healthyChecksAfterRestart >= 2) {
          clearInterval(healthInterval);
          window.location.reload();
        }
      } catch (e) {
        sawRestart = true;
        healthyChecksAfterRestart = 0;
        if (btnApply) {
          btnApply.textContent = window._('js.panel_restarting_waiting');
        }
      }
    }, 2000);
  }

  // Event Listeners
  if (btnCheck) {
    btnCheck.addEventListener('click', () => checkUpdates(true));
  }
  if (btnApply) {
    btnApply.addEventListener('click', applyUpdate);
  }
  if (chkAutoUpdate) {
    chkAutoUpdate.addEventListener('change', (e) => toggleAutoUpdate(e.target.checked));
  }

  // Initial light check on page load
  checkUpdates(false);
});
