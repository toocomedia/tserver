// railpack-app-detail.js — App Engine application detail page controller
const deployment = document.querySelector('[data-railpack-deployment]');
let isPolling = false;

// ── Tab Management ────────────────────────────────────────────────
function initAppTabs() {
  const tabButtons = document.querySelectorAll('[data-app-tab]');
  const tabPanels = document.querySelectorAll('[data-tab-panel]');
  if (!tabButtons.length || !tabPanels.length) return;

  function activateTab(tabName) {
    if (!tabName) tabName = 'overview';
    tabButtons.forEach(btn => {
      const isTarget = btn.getAttribute('data-app-tab') === tabName;
      btn.classList.toggle('is-active', isTarget);
      btn.setAttribute('aria-selected', isTarget ? 'true' : 'false');
    });
    tabPanels.forEach(panel => {
      const isTarget = panel.getAttribute('data-tab-panel') === tabName;
      panel.classList.toggle('is-active', isTarget);
    });
    try {
      sessionStorage.setItem('app_detail_active_tab', tabName);
      if (window.location.hash !== '#' + tabName) {
        history.replaceState(null, '', '#' + tabName);
      }
    } catch (_) {}
  }

  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const tabName = btn.getAttribute('data-app-tab');
      activateTab(tabName);
    });
  });

  // Switch buttons inside content (e.g. "View Logs", "Review & Apply")
  document.querySelectorAll('[data-switch-to-tab]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const target = btn.getAttribute('data-switch-to-tab');
      activateTab(target);
    });
  });

  // Initial tab from hash, query param, or session storage
  const hash = (window.location.hash || '').replace('#', '');
  const storedTab = sessionStorage.getItem('app_detail_active_tab');
  const urlParams = new URLSearchParams(window.location.search);
  const deploymentRequested = urlParams.get('deployment');

  if (deploymentRequested) {
    activateTab('deployments');
  } else if (hash && document.querySelector(`[data-tab-panel="${hash}"]`)) {
    activateTab(hash);
  } else if (storedTab && document.querySelector(`[data-tab-panel="${storedTab}"]`)) {
    activateTab(storedTab);
  } else {
    activateTab('overview');
  }
}
initAppTabs();

// ── Expandable Text (Show More / Show Less) ───────────────────────
document.querySelectorAll('[data-expand-toggle]').forEach(btn => {
  btn.addEventListener('click', () => {
    const parent = btn.closest('.expandable-text');
    if (!parent) return;
    const isExpanded = parent.classList.toggle('is-expanded');
    btn.textContent = isExpanded ? 'Show less' : 'Show more';
  });
});

// Initial scroll to bottom
const initialOut = deployment?.querySelector('[data-deployment-output]');
if (initialOut) {
  initialOut.scrollTop = initialOut.scrollHeight;
}

async function poll() {
  if (!deployment || !deployment.dataset.deploymentUrl) return;
  isPolling = true;
  try {
    const response = await fetch(deployment.dataset.deploymentUrl);
    const item = await response.json();
    if (!response.ok) throw new Error(item.detail || 'Could not refresh deployment.');
    const stateEl = deployment.querySelector('[data-deployment-state]');
    const outEl = deployment.querySelector('[data-deployment-output]');
    if (stateEl) stateEl.textContent = `${item.status} · ${item.stage}`;
    if (outEl) {
      outEl.textContent = item.output + (item.error ? `\n[error] ${item.error}` : '');
      outEl.scrollTop = outEl.scrollHeight;
    }
    if (['queued', 'running'].includes(item.status)) {
      setTimeout(poll, 1500);
    } else {
      isPolling = false;
      setTimeout(() => window.location.reload(), 1500);
    }
  } catch (_) {
    setTimeout(poll, 4000);
  }
}

window.startRailpackDeploymentPolling = function (url) {
  if (deployment && url) {
    deployment.dataset.deploymentUrl = url;
    deployment.dataset.deploymentActive = 'true';
  }
  if (!isPolling) poll();
};

if (deployment?.dataset.deploymentActive === 'true') setTimeout(poll, 800);

const editModal = document.querySelector('[data-edit-settings-modal]');
const editOpenBtn = document.querySelector('[data-edit-settings-open]');
const editCloseBtns = document.querySelectorAll('[data-edit-settings-close]');

if (editModal && editOpenBtn) {
  editOpenBtn.addEventListener('click', () => {
    editModal.classList.remove('hidden');
  });
  editCloseBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      editModal.classList.add('hidden');
    });
  });
  editModal.addEventListener('click', (e) => {
    if (e.target === editModal) editModal.classList.add('hidden');
  });

  const addMountBtn = editModal.querySelector('[data-edit-add-mount]');
  const mountsList = editModal.querySelector('[data-edit-mounts-list]');
  const storageMountsInput = editModal.querySelector('[data-edit-storage-mounts]');
  const editForm = editModal.querySelector('form');

  if (addMountBtn && mountsList) {
    addMountBtn.addEventListener('click', () => {
      const row = document.createElement('div');
      row.style.cssText = 'display: flex; gap: 8px; align-items: center;';
      row.dataset.editMountRow = '';

      const labelInp = document.createElement('input');
      labelInp.className = 'form-input form-input--code';
      labelInp.style.flex = '1';
      labelInp.placeholder = 'Label';
      labelInp.dataset.editMountLabel = '';

      const pathInp = document.createElement('input');
      pathInp.className = 'form-input form-input--code';
      pathInp.style.flex = '1.5';
      pathInp.placeholder = 'Mount Path (e.g. /app/uploads)';
      pathInp.dataset.editMountPath = '';

      const rmBtn = document.createElement('button');
      rmBtn.className = 'btn btn--secondary btn--sm';
      rmBtn.type = 'button';
      rmBtn.textContent = 'Remove';
      rmBtn.addEventListener('click', () => row.remove());

      row.append(labelInp, pathInp, rmBtn);
      mountsList.appendChild(row);
    });

    mountsList.querySelectorAll('[data-edit-mount-remove]').forEach(btn => {
      btn.addEventListener('click', () => {
        btn.closest('[data-edit-mount-row]')?.remove();
      });
    });
  }

  if (editForm && storageMountsInput && mountsList) {
    editForm.addEventListener('submit', (e) => {
      const mounts = [];
      mountsList.querySelectorAll('[data-edit-mount-row]').forEach(row => {
        const label = row.querySelector('[data-edit-mount-label]')?.value.trim().toLowerCase() || '';
        const path = row.querySelector('[data-edit-mount-path]')?.value.trim() || '';
        if (label || path) {
          mounts.push({ label, mount_path: path });
        }
      });
      storageMountsInput.value = JSON.stringify(mounts);
    });
  }
}

document.querySelectorAll('[data-ai-diagnose-app]').forEach(btn => {
  btn.addEventListener('click', () => {
    if (!window.AiHelper) return;
    const appId = btn.getAttribute('data-ai-diagnose-app');
    const appName = btn.getAttribute('data-app-name') || ('App #' + appId);
    const outputEl = document.querySelector('[data-deployment-output]');
    const logSnippet = outputEl ? outputEl.textContent.slice(-2000) : '';
    const prompt = `Application ${appName} (ID #${appId}) failed or is stopped.\nRecent logs:\n\`\`\`log\n${logSnippet}\n\`\`\`\nDiagnose source-aware root cause and create a review-only deployment draft if change is justified. Do not deploy or expose secret values.`;
    window.AiHelper.open({
      split: true,
      taskType: 'app_redeploy',
      context: `App #${appId} (${appName})`,
      initialPrompt: prompt,
    });
  });
});

const csrfToken = document.querySelector('[name="csrf_token"]')?.value
  || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

async function revealCredential(row, action) {
  const response = await fetch(`${row.dataset.revealUrl}?action=${encodeURIComponent(action)}`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken, Accept: 'application/json' },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Could not reveal credentials.');
  row.querySelector('[data-access-password]').textContent = data.password;
  return data.password;
}

document.querySelectorAll('[data-access-credential]').forEach(row => {
  row.querySelector('[data-credential-show]')?.addEventListener('click', async () => {
    try { await revealCredential(row, 'reveal'); } catch (error) { window.alert(error.message); }
  });
  row.querySelector('[data-credential-copy]')?.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(await revealCredential(row, 'copy')); } catch (error) { window.alert(error.message); }
  });
});

document.querySelectorAll('[data-copy-text]').forEach(btn => {
  btn.addEventListener('click', async () => {
    const text = btn.getAttribute('data-copy-text');
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const orig = btn.textContent;
      btn.textContent = 'Copied!';
      btn.classList.add('btn--accent');
      setTimeout(() => {
        btn.textContent = orig;
        btn.classList.remove('btn--accent');
      }, 1500);
    } catch (_) {
      window.prompt('Copy command:', text);
    }
  });
});

