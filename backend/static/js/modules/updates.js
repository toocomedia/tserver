/**
 * updates.js — Frontend module for Git update check & deployment.
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
  const elStatusMsg = document.getElementById('update-status-msg');
  const elStatusBanner = document.getElementById('update-status-banner');

  const modalProgress = document.getElementById('modal-update-progress');
  const logOutput = document.getElementById('update-log-output');
  const reconnectStatus = document.getElementById('update-reconnect-status');

  if (!btnCheck) return; // Not on settings page

  let isPollingLogs = false;

  async function checkUpdates(force = false) {
    if (btnCheckText) btnCheckText.textContent = 'Checking...';
    if (btnCheck) btnCheck.disabled = true;

    try {
      const url = force ? '/api/updates/check?force=true' : '/api/updates/check';
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Render info
      if (elLocalCommit) elLocalCommit.innerHTML = `<code>${data.local_short_sha || 'unknown'}</code>`;
      if (elRemoteCommit) elRemoteCommit.innerHTML = `<code>${data.remote_short_sha || 'unknown'}</code>`;
      if (elCommitMsg) elCommitMsg.textContent = data.commit_message || '—';
      if (elLastChecked) elLastChecked.textContent = data.last_checked || 'Just now';
      if (chkAutoUpdate && typeof data.auto_update_enabled === 'boolean') {
        chkAutoUpdate.checked = data.auto_update_enabled;
      }

      // Status badge & banner
      if (data.has_update) {
        if (elStatusBadge) {
          elStatusBadge.className = 'badge badge--error badge--dot';
          elStatusBadge.textContent = 'Update Available';
        }
        if (elStatusMsg) elStatusMsg.innerHTML = '<strong>New update available!</strong> A new release is ready for installation.';
        if (elStatusBanner) elStatusBanner.className = 'alert alert--danger mb-lg';
        if (btnApply) {
          btnApply.disabled = false;
          btnApply.textContent = 'Update & Restart Panel';
        }
      } else {
        if (elStatusBadge) {
          elStatusBadge.className = 'badge badge--ok badge--dot';
          elStatusBadge.textContent = 'Up to Date';
        }
        if (elStatusMsg) elStatusMsg.innerHTML = '<strong>Up to Date!</strong> Your panel is running the latest code.';
        if (elStatusBanner) elStatusBanner.className = 'alert alert--success mb-lg';
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
      const csrfToken = getCookie('csrftoken') || '';
      const res = await fetch('/api/updates/auto-update', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      console.error('Failed to update auto-update setting:', err);
      alert(`Could not save auto-update setting: ${err.message}`);
      if (chkAutoUpdate) chkAutoUpdate.checked = !enabled;
    }
  }

  async function applyUpdate() {
    if (!confirm('Are you sure you want to update and restart the panel now?\n\nA database backup will be created automatically.')) {
      return;
    }

    // Show progress modal
    if (modalProgress) modalProgress.style.display = 'flex';
    if (logOutput) logOutput.textContent = 'Initializing update runner...\n';
    if (btnApply) btnApply.disabled = true;

    try {
      const csrfToken = getCookie('csrftoken') || '';
      const res = await fetch('/api/updates/apply', {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
      });
      const data = await res.json();
      if (data.status === 'error') {
        alert(data.message);
        if (modalProgress) modalProgress.style.display = 'none';
        return;
      }

      // Start log polling
      startPollingLogs();
    } catch (err) {
      console.error('Apply update trigger failed:', err);
      if (logOutput) logOutput.textContent += `\nError launching update: ${err.message}\n`;
      // Service might be restarting right now — proceed to health check polling
      startPollingHealth();
    }
  }

  function startPollingLogs() {
    if (isPollingLogs) return;
    isPollingLogs = true;

    const interval = setInterval(async () => {
      try {
        const res = await fetch('/api/updates/status');
        if (res.ok) {
          const data = await res.json();
          if (logOutput && data.log) {
            logOutput.textContent = data.log;
            logOutput.scrollTop = logOutput.scrollHeight;
          }
          if (!data.is_updating && data.log.includes('=== Update finished')) {
            clearInterval(interval);
            isPollingLogs = false;
            startPollingHealth();
          }
        }
      } catch (e) {
        // Fetch failed because service is restarting!
        clearInterval(interval);
        isPollingLogs = false;
        startPollingHealth();
      }
    }, 1500);
  }

  function startPollingHealth() {
    if (reconnectStatus) {
      reconnectStatus.innerHTML = '<span class="spinner"></span> Restarting srv-panel service... Reconnecting...';
    }

    const healthInterval = setInterval(async () => {
      try {
        const res = await fetch('/api/health');
        if (res.ok) {
          clearInterval(healthInterval);
          if (reconnectStatus) {
            reconnectStatus.innerHTML = '<span style="color:#00ffaa;">✓ Panel updated and back online! Reloading...</span>';
          }
          setTimeout(() => {
            window.location.reload();
          }, 1500);
        }
      } catch (e) {
        // Still restarting...
      }
    }, 2000);
  }

  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return '';
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
