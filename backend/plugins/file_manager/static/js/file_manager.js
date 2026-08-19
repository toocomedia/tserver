import * as api from './api.js';
import * as ui from './ui.js';

const state = {
  appId: null,
  rootId: null,
  activeRoot: null,
  roots: [],
  path: '',
  entries: [],
  selectedEntries: new Set(),
  sortCol: 'name',
  sortAsc: true
};

let textEditorFile = null;
let aceEditor = null;

// Initialize the Ace editor
document.addEventListener('DOMContentLoaded', () => {
  if (window.ace) {
    aceEditor = ace.edit("editor-textarea");
    aceEditor.setTheme("ace/theme/chrome");
    aceEditor.session.setMode("ace/mode/text");
    aceEditor.setOptions({
      fontSize: "14px",
      showPrintMargin: false,
      wrap: true,
      useWorker: false
    });
  }
});

async function init() {
  setupGlobalListeners();
  try {
    const data = await api.fetchApps();
    const appSelector = document.getElementById('app-selector');
    const quickTargetsContainer = document.getElementById('fm-quick-targets');
    const t = (key, fallback) => (typeof window._ === 'function' ? window._(key) : fallback) || fallback;
    
    if (!data.apps || data.apps.length === 0) {
      appSelector.innerHTML = `<option value="">${t('no_targets_available', 'No targets available')}</option>`;
      const emptyTitle = document.querySelector('#fm-select-app-state .empty-state-strict__title');
      const emptyDesc = document.querySelector('#fm-select-app-state .empty-state-strict__desc');
      if (emptyTitle) emptyTitle.textContent = t('no_targets_available', 'No targets available');
      if (emptyDesc) emptyDesc.textContent = t('no_targets_desc', 'Create a PHP website, Python app, or container first.');
      if (quickTargetsContainer) {
        quickTargetsContainer.innerHTML = `
          <a href="/php-sites/create" class="btn btn--primary btn--sm">+ ${t('create_php_site', 'Create PHP Website')}</a>
          <a href="/apps" class="btn btn--secondary btn--sm">${t('apps_engine', 'Apps Engine')}</a>
        `;
      }
    } else {
      appSelector.innerHTML = `<option value="">${t('select_target', 'Select Target...')}</option>`;
      
      const groups = {
        php: { label: t('hosted_php_websites', 'PHP Websites'), opts: [] },
        container: { label: t('apps_engine', 'Apps Engine'), opts: [] },
        python: { label: t('hosted_python_apps', 'Python Hosted Apps'), opts: [] },
        static: { label: t('static_sites', 'Static Sites'), opts: [] }
      };
      
      if (quickTargetsContainer) {
        quickTargetsContainer.innerHTML = '';
        data.apps.slice(0, 5).forEach(app => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn--secondary btn--sm';
          btn.textContent = app.domain || 'Unnamed';
          btn.onclick = () => {
            appSelector.value = app.id;
            onAppChange({ target: appSelector });
          };
          quickTargetsContainer.appendChild(btn);
        });
      }

      data.apps.forEach(app => {
        const type = app.target_type || 'container';
        if (!groups[type]) groups[type] = { label: type.toUpperCase(), opts: [] };
        
        const opt = document.createElement('option');
        opt.value = app.id;
        opt.textContent = `${app.domain || 'Unnamed'} (${app.preset || app.target_type})`;
        groups[type].opts.push(opt);
      });
      
      for (const [key, group] of Object.entries(groups)) {
        if (group.opts.length > 0) {
          const optgroup = document.createElement('optgroup');
          optgroup.label = group.label;
          group.opts.forEach(opt => optgroup.appendChild(opt));
          appSelector.appendChild(optgroup);
        }
      }
    }
    
    appSelector.disabled = false;
    appSelector.addEventListener('change', onAppChange);
    if (window.hideSkeleton) {
      window.hideSkeleton('fm-skeleton');
    }

    const urlParams = new URLSearchParams(window.location.search);
    const targetParam = urlParams.get('target') || urlParams.get('app');
    const rootParam = urlParams.get('root');
    const pathParam = urlParams.get('path');

    if (targetParam && data.apps && data.apps.some(a => a.id === targetParam)) {
      appSelector.value = targetParam;
      state.appId = targetParam;
      if (pathParam) {
        state.path = pathParam.replace(/^\/+|\/+$/g, '');
      }
      await loadRoots(rootParam);
    } else {
      if (targetParam && data.apps && data.apps.length > 0) {
        if (window.toast) {
          window.toast(`Target "${targetParam}" not found or unavailable.`, 'warning');
        }
      }
      if (!state.appId) {
        document.getElementById('fm-select-app-state').style.display = 'flex';
      }
    }
  } catch (err) {
    if (window.toast) window.toast('Failed to load apps: ' + err.message, 'error');
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

window.handleRowCheck = (cb, entry) => {
  if (cb.checked) {
    state.selectedEntries.add(entry.name);
  } else {
    state.selectedEntries.delete(entry.name);
  }
  if (window.updateBulkDeleteUI) window.updateBulkDeleteUI();
};

window.toggleSelectAll = (cb) => {
  const isChecked = cb.checked;
  const checkboxes = document.querySelectorAll('.row-checkbox');
  checkboxes.forEach(c => {
    c.checked = isChecked;
    if (isChecked) {
      state.selectedEntries.add(c.value);
    } else {
      state.selectedEntries.delete(c.value);
    }
  });
  if (window.updateBulkDeleteUI) window.updateBulkDeleteUI();
};

window.updateBulkDeleteUI = () => {
  const count = state.selectedEntries.size;
  const btn = document.getElementById('btn-bulk-delete');
  const countSpan = document.getElementById('bulk-delete-count');
  if (count > 0) {
    btn.style.display = 'inline-flex';
    if (countSpan) countSpan.textContent = count;
  } else {
    btn.style.display = 'none';
  }
  
  const cbAll = document.getElementById('cb-select-all');
  if (cbAll) {
    const total = Array.from(document.querySelectorAll('.row-checkbox')).length;
    cbAll.checked = count > 0 && count === total;
  }
};

window.openBulkDeleteModal = () => {
  if (state.selectedEntries.size === 0) return;
  window.confirmAction(`Are you sure you want to delete ${state.selectedEntries.size} selected items?`, async () => {
    const btn = document.getElementById('btn-bulk-delete');
    btn.classList.add('is-loading');
    btn.disabled = true;
    try {
      for (const name of state.selectedEntries) {
        const path = (state.path ? state.path + '/' : '') + name;
        await api.deleteFile(state.appId, state.rootId, path, `DELETE ${path}`);
      }
      state.selectedEntries.clear();
      window.updateBulkDeleteUI();
      loadEntries();
    } catch (err) {
      window.toast(`Bulk delete error: ${err.message}`, 'error');
    } finally {
      btn.classList.remove('is-loading');
      btn.disabled = false;
    }
  }, { danger: true, title: "Delete Selected Items", okLabel: "Delete Items", itemName: `${state.selectedEntries.size} Items` });
};

function setupGlobalListeners() {
  document.getElementById('btn-refresh').onclick = () => loadEntries(true);
  document.getElementById('th-name').onclick = () => handleSort('name');
  document.getElementById('th-size').onclick = () => handleSort('size');
  document.getElementById('th-modified').onclick = () => handleSort('modified');
  document.getElementById('btn-new-folder').onclick = () => {
    document.getElementById('new-folder-name').value = '';
    document.getElementById('new-folder-error').style.display = 'none';
    openModal('modal-new-folder');
  };
  document.getElementById('form-new-folder').onsubmit = handleNewFolder;
  
  document.getElementById('btn-new-file').onclick = () => {
    document.getElementById('new-file-name').value = '';
    document.getElementById('new-file-error').style.display = 'none';
    openModal('modal-new-file');
  };
  document.getElementById('form-new-file').onsubmit = handleNewFile;
  
  document.getElementById('btn-upload').onclick = () => {
    document.getElementById('fm-file-input').click();
  };
  document.getElementById('fm-file-input').onchange = handleUpload;
  
  document.getElementById('btn-save-text').onclick = handleSaveText;
  document.getElementById('form-transfer').onsubmit = handleTransfer;
  document.getElementById('form-delete-file').onsubmit = handleDelete;
}

async function onAppChange(e) {
  state.appId = e.target.value;
  state.rootId = null;
  state.path = '';
  
  if (window.history && window.history.replaceState) {
    const url = new URL(window.location.href);
    if (state.appId) {
      url.searchParams.set('target', state.appId);
      url.searchParams.delete('root');
      url.searchParams.delete('path');
    } else {
      url.searchParams.delete('target');
      url.searchParams.delete('app');
      url.searchParams.delete('root');
      url.searchParams.delete('path');
    }
    window.history.replaceState(null, '', url.toString());
  }

  if (!state.appId) {
    resetView();
    return;
  }
  
  await loadRoots();
}

async function loadRoots(preferredRootId = null) {
  if (!state.appId) return;
  document.getElementById('fm-select-app-state').style.display = 'none';
  document.getElementById('fm-table-wrap').style.display = 'none';
  document.getElementById('fm-empty-state').style.display = 'none';
  document.getElementById('fm-toolbar').style.display = 'none';

  try {
    const data = await api.fetchRoots(state.appId);
    state.roots = data.roots || [];
    if (state.roots.length > 0) {
      const targetRootId = (preferredRootId && state.roots.some(r => r.id === preferredRootId))
        ? preferredRootId
        : state.roots[0].id;
      ui.renderRootsTabs(state.roots, targetRootId, onRootSelect);
      onRootSelect(targetRootId, Boolean(state.path));
    } else {
      resetView();
    }
  } catch (err) {
    window.toast('Failed to load roots: ' + err.message, 'error');
  }
}

function onRootSelect(rootId, preservePath = false) {
  state.rootId = rootId;
  state.activeRoot = state.roots.find(r => r.id === rootId);
  if (!preservePath) {
    state.path = '';
  }
  ui.renderRootsTabs(state.roots, state.rootId, onRootSelect);
  
  if (window.history && window.history.replaceState) {
    const url = new URL(window.location.href);
    if (state.appId) {
      url.searchParams.set('target', state.appId);
      if (state.rootId) url.searchParams.set('root', state.rootId);
      if (state.path) url.searchParams.set('path', state.path);
      else url.searchParams.delete('path');
    }
    window.history.replaceState(null, '', url.toString());
  }

  // Disable new folder and new file for runtime-env root per rules
  const btnNewFolder = document.getElementById('btn-new-folder');
  const btnNewFile = document.getElementById('btn-new-file');
  if (state.activeRoot && state.activeRoot.kind === 'environment') {
    btnNewFolder.style.display = 'none';
    btnNewFile.style.display = 'none';
  } else {
    btnNewFolder.style.display = '';
    btnNewFile.style.display = '';
  }
  
  loadEntries();
}

function resetView() {
  document.getElementById('roots-tabs').style.display = 'none';
  document.getElementById('fm-toolbar').style.display = 'none';
  document.getElementById('fm-table-wrap').style.display = 'none';
  document.getElementById('fm-empty-state').style.display = 'none';
  document.getElementById('fm-select-app-state').style.display = 'flex';
}

async function loadEntries(isRefresh = false) {
  setControlsEnabled(false);
  
  const tbody = document.getElementById('fm-tbody');
  document.getElementById('fm-select-app-state').style.display = 'none';
  document.getElementById('fm-toolbar').style.display = 'flex';
  document.getElementById('fm-empty-state').style.display = 'none';
  document.getElementById('fm-table-wrap').style.display = 'block';
  
  // Show localized skeleton loader inside the table while fetching
  tbody.innerHTML = Array.from({length: 4}).map(() => `
    <tr>
      <td></td>
      <td><div class="skeleton-line" style="width: 50%;"></div></td>
      <td><div class="skeleton-line" style="width: 70px;"></div></td>
      <td><div class="skeleton-line" style="width: 120px;"></div></td>
      <td></td>
    </tr>
  `).join('');

  try {
    const data = await api.fetchEntries(state.appId, state.rootId, state.path);
    state.entries = data.entries || [];
    
    ui.renderBreadcrumbs(state.path, (newPath) => {
      state.path = newPath;
      if (window.history && window.history.replaceState) {
        const url = new URL(window.location.href);
        if (state.path) url.searchParams.set('path', state.path);
        else url.searchParams.delete('path');
        window.history.replaceState(null, '', url.toString());
      }
      loadEntries();
    });
    
    state.selectedEntries = new Set();
    if (window.updateBulkDeleteUI) window.updateBulkDeleteUI();
    
    renderEntries();
  } catch (err) {
    handleApiError(err);
  } finally {
    setControlsEnabled(true);
  }
}

function handleSort(col) {
  if (state.sortCol === col) {
    state.sortAsc = !state.sortAsc;
  } else {
    state.sortCol = col;
    state.sortAsc = true;
  }
  renderEntries();
}

function renderEntries() {
  const tbody = document.getElementById('fm-tbody');
  tbody.innerHTML = '';
  
  if (state.entries.length === 0) {
    document.getElementById('fm-empty-state').style.display = 'flex';
    document.getElementById('fm-table-wrap').style.display = 'none';
    return;
  }
  
  document.getElementById('fm-empty-state').style.display = 'none';
  document.getElementById('fm-table-wrap').style.display = 'block';

  ['name', 'size', 'modified'].forEach(c => {
    const icon = document.getElementById(`sort-icon-${c}`);
    if (icon) icon.innerHTML = state.sortCol === c ? (state.sortAsc ? ' ↑' : ' ↓') : '';
  });

  const sorted = [...state.entries].sort((a, b) => {
    if (state.sortCol === 'name') {
      if (a.kind === 'directory' && b.kind !== 'directory') return state.sortAsc ? -1 : 1;
      if (a.kind !== 'directory' && b.kind === 'directory') return state.sortAsc ? 1 : -1;
      return state.sortAsc ? a.name.localeCompare(b.name) : b.name.localeCompare(a.name);
    } else if (state.sortCol === 'size') {
      const sizeA = a.kind === 'directory' ? -1 : (a.size || 0);
      const sizeB = b.kind === 'directory' ? -1 : (b.size || 0);
      return state.sortAsc ? sizeA - sizeB : sizeB - sizeA;
    } else if (state.sortCol === 'modified') {
      const modA = a.modified_at || 0;
      const modB = b.modified_at || 0;
      return state.sortAsc ? modA - modB : modB - modA;
    }
    return 0;
  });

  sorted.forEach(entry => tbody.appendChild(ui.createEntryRow(entry, onEntryAction)));
  if (window.lucide) window.lucide.createIcons();
}

function setControlsEnabled(enabled) {
  const btns = ['btn-new-folder', 'btn-new-file', 'btn-upload', 'btn-refresh'];
  btns.forEach(id => document.getElementById(id).disabled = !enabled);
}

function handleApiError(err) {
  if (err.status === 409) {
    loadRoots();
  } else {
    window.toast(err.message, 'error');
  }
}

function onEntryAction(action, entry) {
  if (action === 'open') {
    state.path = (state.path ? state.path + '/' : '') + entry.name;
    if (window.history && window.history.replaceState) {
      const url = new URL(window.location.href);
      url.searchParams.set('path', state.path);
      window.history.replaceState(null, '', url.toString());
    }
    loadEntries();
  } else if (action === 'edit') {
    openEditor(entry);
  } else if (action === 'download') {
    if (entry.sensitive && !confirm('Warning: This file contains sensitive environment data. Download anyway?')) return;
    window.location.href = `/plugins/file_manager/api/apps/${encodeURIComponent(state.appId)}/roots/${encodeURIComponent(state.rootId)}/download?path=${encodeURIComponent(getEntryPath(entry))}`;
  } else if (action === 'rename' || action === 'copy') {
    document.getElementById('transfer-action').value = action;
    document.getElementById('transfer-source').value = getEntryPath(entry);
    document.getElementById('transfer-dest').value = getEntryPath(entry);
    document.getElementById('transfer-error').style.display = 'none';
    document.getElementById('modal-transfer-title').textContent = action === 'copy' ? 'Copy Item' : 'Rename / Move Item';
    openModal('modal-transfer');
  } else if (action === 'delete') {
    const p = getEntryPath(entry);
    if (typeof window.openDeleteDrawer === "function") {
      window.openDeleteDrawer({
        title: "Delete File / Folder",
        message: `Are you sure you want to delete this item?`,
        itemName: p,
        okLabel: "Delete",
        onConfirm: async () => {
          try {
            await api.deleteFile(state.appId, state.rootId, p, `DELETE ${p}`);
            loadEntries();
          } catch (err) {
            window.toast(`Delete error: ${err.message}`, 'error');
          }
        }
      });
    } else {
      document.getElementById('delete-file-path').value = p;
      document.getElementById('delete-prompt-label').textContent = `Type "DELETE ${p}" to confirm`;
      document.getElementById('delete-confirmation').value = '';
      document.getElementById('delete-file-error').style.display = 'none';
      openModal('modal-delete-file');
    }
  } else if (action === 'properties') {
    showProperties(entry);
  }
}

function getEntryPath(entry) {
  return (state.path ? state.path + '/' : '') + entry.name;
}

// === Actions ===

async function handleNewFolder(e) {
  e.preventDefault();
  const name = document.getElementById('new-folder-name').value.trim();
  if (!name) return;
  const targetPath = (state.path ? state.path + '/' : '') + name;
  const btn = document.getElementById('btn-submit-new-folder');
  btn.classList.add('is-loading');
  try {
    await api.createDirectory(state.appId, state.rootId, targetPath);
    closeModal('modal-new-folder');
    loadEntries();
  } catch (err) {
    const errDiv = document.getElementById('new-folder-error');
    errDiv.textContent = err.message;
    errDiv.style.display = 'block';
  } finally {
    btn.classList.remove('is-loading');
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    if (btn.dataset.originalLabel) {
      btn.textContent = btn.dataset.originalLabel;
      delete btn.dataset.originalLabel;
    }
    const form = btn.closest('form');
    if (form) form.removeAttribute('data-submitting');
  }
}

async function handleNewFile(e) {
  e.preventDefault();
  const name = document.getElementById('new-file-name').value.trim();
  if (!name) return;
  const targetPath = (state.path ? state.path + '/' : '') + name;
  const btn = document.getElementById('btn-submit-new-file');
  btn.classList.add('is-loading');
  try {
    await api.saveText(state.appId, state.rootId, targetPath, "", null);
    closeModal('modal-new-file');
    loadEntries();
  } catch (err) {
    const errDiv = document.getElementById('new-file-error');
    errDiv.textContent = err.message;
    errDiv.style.display = 'block';
  } finally {
    btn.classList.remove('is-loading');
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
  }
}

async function handleUpload(e) {
  const file = e.target.files[0];
  if (!file) return;
  const targetPath = (state.path ? state.path + '/' : '') + file.name;
  setControlsEnabled(false);
  try {
    await api.uploadFile(state.appId, state.rootId, targetPath, file);
    loadEntries();
  } catch (err) {
    if (err.status === 409) {
      if (confirm(`Conflict: ${err.message}. Overwrite?`)) {
        // We'd need etag to overwrite, which we don't have easily here.
        // Let's just show the error for now per simple requirements.
        window.toast(err.message, 'error');
      }
    } else {
      window.toast(err.message, 'error');
    }
  } finally {
    e.target.value = '';
    setControlsEnabled(true);
  }
}

async function handleTransfer(e) {
  e.preventDefault();
  const action = document.getElementById('transfer-action').value;
  const source = document.getElementById('transfer-source').value;
  const dest = document.getElementById('transfer-dest').value.trim();
  const btn = document.getElementById('btn-submit-transfer');
  btn.classList.add('is-loading');
  try {
    await api.transferFile(state.appId, state.rootId, action, source, dest);
    closeModal('modal-transfer');
    loadEntries();
  } catch (err) {
    const errDiv = document.getElementById('transfer-error');
    errDiv.textContent = err.message;
    errDiv.style.display = 'block';
  } finally {
    btn.classList.remove('is-loading');
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
  }
}

async function handleDelete(e) {
  e.preventDefault();
  const path = document.getElementById('delete-file-path').value;
  const confirmation = document.getElementById('delete-confirmation').value;
  const btn = document.getElementById('btn-submit-delete');
  btn.classList.add('is-loading');
  try {
    await api.deleteFile(state.appId, state.rootId, path, confirmation);
    closeModal('modal-delete-file');
    loadEntries();
  } catch (err) {
    const errDiv = document.getElementById('delete-file-error');
    errDiv.textContent = err.message;
    errDiv.style.display = 'block';
  } finally {
    btn.classList.remove('is-loading');
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    if (btn.dataset.originalLabel) {
      btn.textContent = btn.dataset.originalLabel;
      delete btn.dataset.originalLabel;
    }
    const form = btn.closest('form');
    if (form) form.removeAttribute('data-submitting');
  }
}

// === Editor ===

async function openEditor(entry) {
  const path = getEntryPath(entry);
  if (entry.sensitive && !confirm('Warning: This file contains sensitive environment data. Open anyway?')) return;
  
  try {
    const data = await api.fetchText(state.appId, state.rootId, path);
    textEditorFile = { path, etag: data.etag };
    
    if (aceEditor) {
      const modelist = ace.require("ace/ext/modelist");
      if (modelist) {
        const mode = modelist.getModeForPath(entry.name).mode;
        aceEditor.session.setMode(mode);
      }
      
      const theme = document.documentElement.getAttribute('data-theme');
      const isDark = theme === 'dark' || theme === 'amoled' || theme === 'charcoal';
      aceEditor.setTheme(isDark ? "ace/theme/one_dark" : "ace/theme/chrome");
      
      aceEditor.setValue(data.content, -1);
    } else {
      document.getElementById('editor-textarea').value = data.content;
    }
    
    const warnDiv = document.getElementById('editor-warning');
    if (state.activeRoot && state.activeRoot.persistence === 'live_runtime') {
      warnDiv.textContent = 'Warning: These files are in a live runtime (container or Python release). A deploy or recreate will replace these edits.';
      warnDiv.style.display = 'block';
    } else {
      warnDiv.style.display = 'none';
    }
    document.getElementById('editor-error').style.display = 'none';
    
    document.getElementById('modal-text-editor-title').textContent = `Edit: ${entry.name}`;
    openModal('modal-text-editor');
  } catch (err) {
    handleApiError(err);
  }
}

async function handleSaveText() {
  if (!textEditorFile) return;
  const content = aceEditor ? aceEditor.getValue() : document.getElementById('editor-textarea').value;
  const btn = document.getElementById('btn-save-text');
  btn.classList.add('is-loading');
  document.getElementById('editor-error').style.display = 'none';
  
  try {
    const data = await api.saveText(state.appId, state.rootId, textEditorFile.path, content, textEditorFile.etag);
    if (data.restart_required) {
      window.toast('Values take effect after the next Apps Engine restart or redeploy.', 'warning');
    } else {
      window.toast('File saved successfully.', 'success');
    }
    closeModal('modal-text-editor');
    loadEntries();
  } catch (err) {
    const errDiv = document.getElementById('editor-error');
    if (err.status === 409) {
      errDiv.innerHTML = `${err.message} <button type="button" class="btn btn--sm btn--secondary" onclick="document.getElementById('btn-save-text').classList.remove('is-loading'); closeModal('modal-text-editor');">Cancel</button>`;
    } else {
      errDiv.textContent = err.message;
    }
    errDiv.style.display = 'block';
  } finally {
    btn.classList.remove('is-loading');
  }
}

// === Properties ===
function showProperties(entry) {
  document.getElementById('prop-name').textContent = entry.name;
  document.getElementById('prop-kind').textContent = entry.kind;
  document.getElementById('prop-size').textContent = entry.kind === 'directory' ? '--' : ui.formatSize(entry.size);
  document.getElementById('prop-modified').textContent = entry.modified_at ? new Date(entry.modified_at * 1000).toLocaleString() : '--';
  document.getElementById('prop-sensitive').textContent = entry.sensitive ? 'Yes' : 'No';
  document.getElementById('prop-persistence').textContent = state.activeRoot ? state.activeRoot.persistence : '--';
  openModal('modal-properties');
}
