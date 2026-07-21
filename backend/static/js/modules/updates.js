/**
 * updates.js — Light Git update check & silent background deployment.
 */
document.addEventListener('DOMContentLoaded', () => {
  const btnCheck = document.getElementById('btn-check-updates');
  const btnCheckText = document.getElementById('btn-check-updates-text');
  const btnApply = document.getElementById('btn-apply-update');
  const chkAutoUpdate = document.getElementById('chk_auto_update');
  
  const elLocalCommit = document.getElementById('update-local-commit');
  const elRemoteCommit = document.getElementById('update-remote-commit');
  const elCommitMsg = document.getElementById('update-commit-msg');
  const elLastChecked = document.getElementById('update-last-checked');
  const elStatusBadge = document.getElementById('update-status-badge');

  if (!btnCheck) return; // Not on settings page

  async function checkUpdates(force = false) {
    if (btnCheckText) btnCheckText.textContent = 'Checking...';
    if (btnCheck) btnCheck.disabled = true;

    try {
      const url = force ? '/api/updates/check?force=true' : '/api/updates/check';
      const data = await window.panel.get(url);

      // Render info
      if (elLocalCommit) elLocalCommit.innerHTML = `<code>${data.local_short_sha || 'unknown'}</code>`;
      if (elRemoteCommit) elRemoteCommit.innerHTML = `<code>${data.remote_short_sha || 'unknown'}</code>`;
      if (elCommitMsg) elCommitMsg.textContent = data.commit_message || '—';
      if (elLastChecked) elLastChecked.textContent = data.last_checked || 'Just now';
      if (chkAutoUpdate && typeof data.auto_update_enabled === 'boolean') {
        chkAutoUpdate.checked = data.auto_update_enabled;
      }

      // Status badge
      if (data.has_update) {
        if (elStatusBadge) {
          elStatusBadge.className = 'badge badge--error badge--dot';
          elStatusBadge.textContent = 'Update Available';
        }
        if (btnApply) {
          btnApply.disabled = false;
          btnApply.textContent = 'Update & Restart Panel';
        }
      } else {
        if (elStatusBadge) {
          elStatusBadge.className = 'badge badge--ok badge--dot';
          elStatusBadge.textContent = 'Up to date';
        }
        if (btnApply) {
          btnApply.disabled = true;
          btnApply.textContent = 'Already Up to Date';
        }
      }
    } catch (err) {
      console.error('Update check failed:', err);
      if (elStatusBadge) {
        elStatusBadge.className = 'badge badge--error';
        elStatusBadge.textContent = 'Check Failed';
      }
      if (elStatusMsg) elStatusMsg.textContent = `Could not verify updates: ${err.message}`;
    } finally {
      if (btnCheckText) btnCheckText.textContent = 'Check for Updates';
      if (btnCheck) btnCheck.disabled = false;
    }
  }

  async function toggleAutoUpdate(enabled) {
    try {
      await window.panel.post('/api/updates/auto-update', { enabled });
      if (typeof window.toast === 'function') {
        window.toast(`Automatic updates ${enabled ? 'enabled' : 'disabled'}.`, 'success');
      }
    } catch (err) {
      console.error('Failed to update auto-update setting:', err);
      if (typeof window.toast === 'function') {
        window.toast(`Could not save auto-update setting: ${err.message}`, 'danger');
      }
      if (chkAutoUpdate) chkAutoUpdate.checked = !enabled;
    }
  }

  async function applyUpdate() {
    if (!confirm('Are you sure you want to update and restart the panel now?\n\nA database backup will be created automatically.')) {
      return;
    }

    if (btnApply) {
      btnApply.disabled = true;
      btnApply.innerHTML = '<span class="spinner" style="display:inline-block; border:2px solid currentColor; border-top-color:transparent; border-radius:50%; width:12px; height:12px; animation:spin 1s linear infinite; margin-right:6px;"></span> Updating in background...';
    }

    try {
      const data = await window.panel.post('/api/updates/apply', {});
      if (data.status === 'error') {
        if (typeof window.toast === 'function') {
          window.toast(data.message, 'danger');
        }
        if (btnApply) {
          btnApply.disabled = false;
          btnApply.textContent = 'Update & Restart Panel';
        }
        return;
      }

      if (typeof window.toast === 'function') {
        window.toast('Update started in background! Database backed up. Panel will restart shortly.', 'success');
      }

      // Start background reconnect polling
      startPollingHealth();
    } catch (err) {
      console.error('Apply update trigger:', err);
      if (typeof window.toast === 'function') {
        window.toast('Update process launched. Reconnecting to server...', 'info');
      }
      startPollingHealth();
    }
  }

  function startPollingHealth() {
    const healthInterval = setInterval(async () => {
      try {
        const res = await fetch('/api/health');
        if (res.ok) {
          clearInterval(healthInterval);
          if (typeof window.toast === 'function') {
            window.toast('Panel updated successfully! Reloading...', 'success');
          }
          setTimeout(() => {
            window.location.reload();
          }, 1500);
        }
      } catch (e) {
        // Service restarting...
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
