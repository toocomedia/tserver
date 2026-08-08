import { csrfHeaders, fetchJson, setHidden, setText } from './railpack-app-create-ui.js';

const section = document.querySelector('[data-app-orphan-recovery]');

if (section) loadOrphans();

async function loadOrphans() {
  try {
    const data = await fetchJson('/plugins/railpack_apps/recovery/orphans', { headers: csrfHeaders() });
    renderOrphans(data.items || []);
  } catch (error) {
    setText(section.querySelector('[data-app-orphan-error]'), error.message || 'Could not check failed deployments.');
    setHidden(section.querySelector('[data-app-orphan-error]'), false);
    setHidden(section, false);
  }
}

function renderOrphans(items) {
  const list = section.querySelector('[data-app-orphan-items]');
  list.replaceChildren(...items.map(orphanRow));
  setHidden(section, items.length === 0);
}

function orphanRow(item) {
  const row = document.createElement('div');
  row.className = 'apps-engine-recovery__row';
  const details = document.createElement('div');
  const title = document.createElement('strong');
  const description = document.createElement('p');
  title.textContent = `Failed app #${item.app_id}: ${item.kind}`;
  description.className = 'text-muted';
  description.textContent = `${item.name} is ${item.state}. Removing it permanently deletes its private database data.`;
  details.append(title, description);
  const button = document.createElement('button');
  button.className = 'btn btn--danger btn--sm';
  button.type = 'button';
  button.textContent = 'Remove failed data';
  button.addEventListener('click', () => confirmRemoval(item, button));
  row.append(details, button);
  return row;
}

function confirmRemoval(item, button) {
  const remove = () => removeOrphan(item, button);
  const message = `Remove ${item.name} and its private database data? This cannot be undone.`;
  if (typeof window.confirmAction === 'function') {
    window.confirmAction(message, remove, { title: 'Remove failed deployment data', okLabel: 'Remove data', danger: true });
  } else if (window.confirm(message)) remove();
}

async function removeOrphan(item, button) {
  button.disabled = true;
  try {
    await fetchJson(`/plugins/railpack_apps/recovery/orphans/${item.app_id}/remove`, { method: 'POST', headers: csrfHeaders() });
    await loadOrphans();
  } catch (error) {
    button.disabled = false;
    setText(section.querySelector('[data-app-orphan-error]'), error.message || 'Removal failed.');
    setHidden(section.querySelector('[data-app-orphan-error]'), false);
  }
}
