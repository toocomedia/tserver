export function renderRootsTabs(roots, activeRootId, onSelectRoot) {
  const container = document.getElementById('roots-tabs');
  container.innerHTML = '';
  if (!roots || roots.length === 0) {
    container.style.display = 'none';
    return;
  }
  container.style.display = 'flex';
  
  roots.forEach(root => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `btn btn--sm ${root.id === activeRootId ? 'btn--primary' : 'btn--secondary'}`;
    btn.textContent = root.name;
    btn.onclick = () => onSelectRoot(root.id);
    container.appendChild(btn);
  });
}

export function renderBreadcrumbs(pathStr, onNavigate) {
  const container = document.getElementById('fm-breadcrumbs');
  container.innerHTML = '';
  
  const rootBtn = document.createElement('a');
  rootBtn.className = !pathStr ? 'fm-breadcrumb-item fm-breadcrumb-item--active' : 'fm-breadcrumb-item';
  rootBtn.textContent = 'Root';
  if (pathStr) rootBtn.onclick = (e) => { e.preventDefault(); onNavigate(''); };
  container.appendChild(rootBtn);

  if (!pathStr) return;
  const parts = pathStr.split('/').filter(p => p);
  let currentPath = '';

  for (let i = 0; i < parts.length; i++) {
    const sep = document.createElement('span');
    sep.className = 'fm-breadcrumb-separator';
    sep.textContent = '/';
    container.appendChild(sep);

    currentPath += (i > 0 ? '/' : '') + parts[i];
    const link = document.createElement('a');
    link.textContent = parts[i];
    const isLast = (i === parts.length - 1);
    link.className = isLast ? 'fm-breadcrumb-item fm-breadcrumb-item--active' : 'fm-breadcrumb-item';
    if (!isLast) {
      const p = currentPath;
      link.onclick = (e) => { e.preventDefault(); onNavigate(p); };
    }
    container.appendChild(link);
  }
}

export function formatSize(bytes) {
  if (bytes === null || bytes === undefined) return '--';
  if (bytes === 0) return '0 B';
  const k = 1024, sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export function createEntryRow(entry, onAction) {
  const tr = document.createElement('tr');
  tr.className = 'fm-row';
  
  const isDir = entry.kind === 'directory';
  const icon = isDir ? 'folder' : (entry.kind === 'symlink' ? 'corner-down-right' : 'file');
  const iconClass = isDir ? 'fm-icon-folder' : (entry.kind === 'symlink' ? 'fm-icon-symlink' : 'fm-icon-file');

  const tdName = document.createElement('td');
  tdName.innerHTML = `<div style="display:flex;align-items:center;gap:8px;"><i data-lucide="${icon}" class="${iconClass}" style="width:16px;height:16px;"></i><span style="font-weight:500;word-break:break-all;">${entry.name}</span></div>`;
  
  if (isDir) {
    tr.onclick = (e) => {
      if (!e.target.closest('button') && !e.target.closest('.list-row-subactions')) onAction('open', entry);
    };
  }

  const tdSize = document.createElement('td');
  tdSize.textContent = isDir ? '--' : formatSize(entry.size);

  const tdMod = document.createElement('td');
  tdMod.textContent = entry.modified_at ? new Date(entry.modified_at * 1000).toLocaleString() : '--';

  const tdActions = document.createElement('td');
  tdActions.className = 'col-actions';
  tdActions.style.position = 'relative';
  tdActions.innerHTML = buildRowActionsHtml(entry);
  
  tr.appendChild(tdName);
  tr.appendChild(tdSize);
  tr.appendChild(tdMod);
  tr.appendChild(tdActions);

  tdActions.querySelectorAll('.list-row-subactions__btns button').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      onAction(btn.dataset.action, entry);
      if (window.toggleListRowActions) {
         const toggleBtn = tdActions.querySelector('.list-actions-toggle-btn');
         if (toggleBtn && toggleBtn.getAttribute('aria-expanded') === 'true') window.toggleListRowActions(toggleBtn);
      }
    };
  });
  
  // Stop propagation on the toggle buttons themselves so they don't trigger row click
  tdActions.querySelectorAll('.list-actions-toggle-btn, .list-row-subactions__close').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      if (window.toggleListRowActions) window.toggleListRowActions(btn);
    }
  });
  
  // Prevent tray clicks from propagating to row
  const tray = tdActions.querySelector('.list-row-subactions');
  if (tray) tray.onclick = (e) => e.stopPropagation();

  return tr;
}

function buildRowActionsHtml(entry) {
  const actions = [];
  if (entry.kind === 'file') {
    actions.push(`<button type="button" class="btn btn--secondary btn--sm" data-action="download" title="Download">Download</button>`);
    actions.push(`<button type="button" class="btn btn--secondary btn--sm" data-action="edit" title="Edit">Edit</button>`);
  }
  if (entry.kind !== 'symlink') {
    actions.push(`<button type="button" class="btn btn--secondary btn--sm" data-action="rename" title="Move/Rename">Rename</button>`);
    actions.push(`<button type="button" class="btn btn--secondary btn--sm" data-action="copy" title="Copy">Copy</button>`);
    actions.push(`<button type="button" class="btn btn--danger btn--sm" data-action="delete" title="Delete">Delete</button>`);
  }
  actions.push(`<button type="button" class="btn btn--secondary btn--sm" data-action="properties" title="Properties">Props</button>`);

  return `
    <button type="button" class="icon-btn list-actions-toggle-btn" aria-expanded="false" title="Actions">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" class="toggle-dots-icon">
        <circle cx="12" cy="12" r="1.5"></circle>
        <circle cx="19" cy="12" r="1.5"></circle>
        <circle cx="5" cy="12" r="1.5"></circle>
      </svg>
    </button>
    <div class="list-row-subactions slide-toolbar is-hidden">
      <div class="list-row-subactions__inner">
        <div class="list-row-subactions__btns">
          ${actions.join('')}
        </div>
        <button type="button" class="btn btn--ghost btn--icon btn--sm list-row-subactions__close" aria-label="Close" title="Close"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"></path></svg></button>
      </div>
    </div>
  `;
}
