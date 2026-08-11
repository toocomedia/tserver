import * as api from './api.js';
import * as ui from './ui.js';

const state = {
  appId: null,
  rootId: null,
  activeRoot: null,
  roots: [],
  path: '',
  entries: []
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
    if (data.apps.length === 0) {
      appSelector.innerHTML = '<option value="">No targets available</option>';
    } else {
      appSelector.innerHTML = '<option value="">Select Target...</option>';
      
      const groups = {
        container: { label: 'Apps Engine', opts: [] },
        python: { label: 'Python Hosted Apps', opts: [] },
        static: { label: 'Static Sites', opts: [] }
      };
      
      data.apps.forEach(app => {
        const type = app.target_type || 'container';
        if (!groups[type]) groups[type] = { label: type.toUpperCase(), opts: [] };
        
        const opt = document.createElement('option');
        opt.value = app.id;
        opt.textContent = `${app.domain || 'Unnamed'} (${app.preset})`;
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
    
    if (!state.appId) {
      document.getElementById('fm-select-app-state').style.display = 'block';
    }
  } catch (err) {
    if (window.showToast) window.showToast('Failed to load apps: ' + err.message, 'error');
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

function setupGlobalListeners() {
  document.getElementById('btn-refresh').onclick = () => loadEntries(true);
  document.getElementById('btn-new-folder').onclick = () => {
    document.getElementById('new-folder-name').value = '';
    document.getElementById('new-folder-error').style.display = 'none';
    openModal('modal-new-folder');
  };
  document.getElementById('form-new-folder').onsubmit = handleNewFolder;
  
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
  
  if (!state.appId) {
    resetView();
    return;
  }
  
  await loadRoots();
}

async function loadRoots() {
  if (!state.appId) return;
  document.getElementById('fm-select-app-state').style.display = 'none';
  document.getElementById('fm-table-wrap').style.display = 'none';
  document.getElementById('fm-empty-state').style.display = 'none';
  document.getElementById('fm-toolbar').style.display = 'none';

  try {
    const data = await api.fetchRoots(state.appId);
    state.roots = data.roots || [];
    ui.renderRootsTabs(state.roots, state.rootId, onRootSelect);
    if (state.roots.length > 0) {
      onRootSelect(state.roots[0].id);
    } else {
      resetView();
    }
  } catch (err) {
    window.showToast('Failed to load roots: ' + err.message, 'error');
  }
}

function onRootSelect(rootId) {
  state.rootId = rootId;
  state.activeRoot = state.roots.find(r => r.id === rootId);
  state.path = '';
  ui.renderRootsTabs(state.roots, state.rootId, onRootSelect);
  
  // Disable new folder for runtime-env root per rules
  const btnNewFolder = document.getElementById('btn-new-folder');
  if (state.activeRoot && state.activeRoot.kind === 'environment') {
    btnNewFolder.style.display = 'none';
  } else {
    btnNewFolder.style.display = '';
  }
  
  loadEntries();
}

function resetView() {
  document.getElementById('roots-tabs').style.display = 'none';
  document.getElementById('fm-toolbar').style.display = 'none';
  document.getElementById('fm-table-wrap').style.display = 'none';
  document.getElementById('fm-empty-state').style.display = 'block';
  document.getElementById('fm-select-app-state').style.display = 'block';
}

async function loadEntries(isRefresh = false) {
  setControlsEnabled(false);
  
  const tbody = document.getElementById('fm-tbody');
  document.getElementById('fm-select-app-state').style.display = 'none';
  document.getElementById('fm-toolbar').style.display = 'flex';
  document.getElementById('fm-empty-state').style.display = 'none';
  document.getElementById('fm-table-wrap').style.display = 'block';
  
  // Show localized skeleton loader inside the table while fetching
  tbody.innerHTML = Array.from({length: 3}).map(() => `
    <tr>
      <td><div class="skeleton-line" style="width: 50%;"></div></td>
      <td><div class="skeleton-line" style="width: 80%;"></div></td>
      <td><div class="skeleton-line" style="width: 60%;"></div></td>
      <td><div class="skeleton-line" style="width: 30%;"></div></td>
    </tr>
  `).join('');

  try {
    const data = await api.fetchEntries(state.appId, state.rootId, state.path);
    state.entries = data.entries || [];
    
    ui.renderBreadcrumbs(state.path, (newPath) => {
      state.path = newPath;
      loadEntries();
    });
    
    tbody.innerHTML = '';
    
    if (state.entries.length === 0) {
      document.getElementById('fm-empty-state').style.display = 'block';
      document.getElementById('fm-table-wrap').style.display = 'none';
    } else {
      document.getElementById('fm-empty-state').style.display = 'none';
      document.getElementById('fm-table-wrap').style.display = 'block';
      state.entries.forEach(entry => tbody.appendChild(ui.createEntryRow(entry, onEntryAction)));
      if (window.lucide) window.lucide.createIcons();
    }
  } catch (err) {
    handleApiError(err);
  } finally {
    setControlsEnabled(true);
  }
}

function setControlsEnabled(enabled) {
  const btns = ['btn-new-folder', 'btn-upload', 'btn-refresh'];
  btns.forEach(id => document.getElementById(id).disabled = !enabled);
}

function handleApiError(err) {
  if (err.status === 409) {
    loadRoots();
  } else {
    window.showToast(err.message, 'error');
  }
}

function onEntryAction(action, entry) {
  if (action === 'open') {
    state.path = (state.path ? state.path + '/' : '') + entry.name;
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
    document.getElementById('delete-file-path').value = getEntryPath(entry);
    document.getElementById('delete-prompt-label').textContent = `Type "DELETE ${getEntryPath(entry)}" to confirm`;
    document.getElementById('delete-confirmation').value = '';
    document.getElementById('delete-file-error').style.display = 'none';
    openModal('modal-delete-file');
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
        window.showToast(err.message, 'error');
      }
    } else {
      window.showToast(err.message, 'error');
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
      window.showToast('Values take effect after the next Apps Engine restart or redeploy.', 'warning');
    } else {
      window.showToast('File saved successfully.', 'success');
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
