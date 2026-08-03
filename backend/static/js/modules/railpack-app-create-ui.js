export const csrfHeaders = () => ({ 'X-CSRF-Token': document.querySelector('[name="csrf_token"]')?.value || '' });

export function setHidden(element, hidden) {
  if (element) element.hidden = hidden;
}

export function setText(element, value) {
  if (element) element.textContent = value;
}

export async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'The request could not be completed.');
  return data;
}

export function addEnvironmentRow(form, key = '', value = '') {
  const list = form.querySelector('[data-environment-list]');
  const row = document.createElement('div');
  const suffix = `${Date.now()}-${list.children.length}`;
  row.className = 'apps-engine-environment-row';
  row.dataset.environmentRow = '';
  row.append(environmentField('Variable name', `environment-key-${suffix}`, 'VARIABLE_NAME', key, 'text'));
  row.append(environmentField('Value', `environment-value-${suffix}`, 'Enter value', value, 'password'));
  const remove = document.createElement('button');
  remove.className = 'btn btn--secondary btn--sm';
  remove.type = 'button';
  remove.textContent = 'Remove';
  remove.addEventListener('click', () => row.remove());
  row.append(remove);
  list.append(row);
}

export function environmentValues(form) {
  const values = {};
  const validKey = /^[A-Z_][A-Z0-9_]{0,127}$/;
  form.querySelectorAll('[data-environment-row]').forEach((row) => {
    const key = row.querySelector('[data-environment-key]').value.trim();
    const value = row.querySelector('[data-environment-value]').value;
    if (!key && !value) return;
    if (!validKey.test(key) || /[\r\n]/.test(value)) throw new Error('Environment variables need an uppercase name and a one-line value.');
    values[key] = value;
  });
  return values;
}

export function renderInspection(form, data) {
  setText(form.querySelector('[data-inspection-runtime]'), data.runtime || 'Source ready');
  setText(form.querySelector('[data-inspection-build]'), data.build_mode || 'Image');
  setText(form.querySelector('[data-inspection-port]'), data.internal_port || 'Configured on the image');
  const databases = data.database_types || [];
  setText(form.querySelector('[data-inspection-databases]'), databases.length ? databases.join(', ') : 'No database detected');
  setText(form.querySelector('[data-inspect-summary]'), data.summary || 'Review these editable source suggestions.');
}

export function renderDeployment(form, data) {
  const state = `${data.status || 'queued'} · ${data.stage || 'waiting'}`;
  const labels = { prepare: 'Preparing deployment', source: 'Cloning Git source', build: 'Building application image', pull: 'Pulling registry image', rollback: 'Restoring previous image', complete: 'Deployment complete' };
  setText(form.querySelector('[data-deployment-state]'), state);
  setText(form.querySelector('[data-deployment-summary]'), labels[data.stage] || 'Deployment is running on the server.');
  setText(form.querySelector('[data-deployment-output]'), `${data.output || ''}${data.error ? `\n[error] ${data.error}` : ''}`);
  renderDeploymentSteps(form.querySelector('[data-deployment-steps]'), data.stage, data.status);
}

function environmentField(labelText, id, placeholder, value, type) {
  const group = document.createElement('div');
  group.className = 'form-group';
  const label = document.createElement('label');
  const input = document.createElement('input');
  label.className = 'form-label';
  label.htmlFor = id;
  label.textContent = labelText;
  input.className = 'form-input form-input--code';
  input.id = id;
  input.placeholder = placeholder;
  input.type = type;
  input.autocomplete = 'off';
  input.value = value;
  if (labelText === 'Variable name') input.dataset.environmentKey = '';
  if (labelText === 'Value') input.dataset.environmentValue = '';
  group.append(label, input);
  return group;
}

function renderDeploymentSteps(container, stage, status) {
  const stages = [['prepare', 'Preparing deployment'], ['source', 'Cloning Git source'], ['build', 'Building application image'], ['pull', 'Pulling registry image'], ['rollback', 'Restoring previous image'], ['complete', 'Deployment complete']];
  container.replaceChildren(...stages.map(([name, label]) => deploymentStep(name, label, stage, status)));
}

function deploymentStep(name, label, stage, status) {
  const row = document.createElement('div');
  row.className = `scroll-step-item ${name === stage ? 'active' : status === 'success' && name === 'complete' ? 'completed' : 'pending'}`;
  const icon = document.createElement('span');
  const text = document.createElement('span');
  icon.className = 'step-icon-wrap';
  text.className = 'step-text';
  text.textContent = label;
  icon.textContent = name === stage && status === 'running' ? '…' : name === 'complete' && status === 'success' ? '✓' : '○';
  row.append(icon, text);
  return row;
}
