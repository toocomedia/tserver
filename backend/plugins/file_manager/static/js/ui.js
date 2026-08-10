export function renderRootsTabs(roots, activeRootId, onSelectRoot) {
  const container = document.getElementById('roots-tabs');
  container.innerHTML = '';
  if (!roots || roots.length === 0) {
    container.style.display = 'none';
    return;
  }
  container.style.display = 'flex';
  
  for (const root of roots) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `tabs__item ${root.id === activeRootId ? 'tabs__item--active' : ''}`;
    btn.textContent = root.name;
    btn.onclick = () => onSelectRoot(root.id);
    container.appendChild(btn);
  }
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
      if (!e.target.closest('button')) onAction('open', entry);
    };
  }

  const tdSize = document.createElement('td');
  tdSize.textContent = isDir ? '--' : formatSize(entry.size);

  const tdMod = document.createElement('td');
  tdMod.textContent = entry.modified_at ? new Date(entry.modified_at * 1000).toLocaleString() : '--';

  const tdActions = document.createElement('td');
  tdActions.style.textAlign = 'right';
  tdActions.innerHTML = buildRowActionsHtml(entry);
  
  tr.appendChild(tdName);
  tr.appendChild(tdSize);
  tr.appendChild(tdMod);
  tr.appendChild(tdActions);

  tdActions.querySelectorAll('button').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      onAction(btn.dataset.action, entry);
    };
  });

  return tr;
}

function buildRowActionsHtml(entry) {
  const actions = [];
  if (entry.kind === 'file') {
    actions.push(`<button class="btn btn--ghost fm-action-btn" data-action="download" title="Download"><i data-lucide="download" style="width:14px;height:14px;"></i></button>`);
    actions.push(`<button class="btn btn--ghost fm-action-btn" data-action="edit" title="Edit"><i data-lucide="edit-2" style="width:14px;height:14px;"></i></button>`);
  }
  if (entry.kind !== 'symlink') {
    actions.push(`<button class="btn btn--ghost fm-action-btn" data-action="rename" title="Move/Rename"><i data-lucide="move" style="width:14px;height:14px;"></i></button>`);
    actions.push(`<button class="btn btn--ghost fm-action-btn" data-action="copy" title="Copy"><i data-lucide="copy" style="width:14px;height:14px;"></i></button>`);
    actions.push(`<button class="btn btn--ghost fm-action-btn" data-action="delete" title="Delete"><i data-lucide="trash-2" style="width:14px;height:14px;color:var(--color-danger);"></i></button>`);
  }
  actions.push(`<button class="btn btn--ghost fm-action-btn" data-action="properties" title="Properties"><i data-lucide="info" style="width:14px;height:14px;"></i></button>`);

  return `<div class="fm-row-actions">${actions.join('')}</div>`;
}
