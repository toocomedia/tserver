export const csrfHeaders = () => ({
  'X-CSRF-Token': document.querySelector('[name="csrf_token"]')?.value || '',
});

export async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'The request could not be completed.');
  return data;
}

export function setHidden(element, hidden) {
  if (element) element.hidden = hidden;
}

export function setText(element, value) {
  if (element) element.textContent = value;
}

export function renderBranches(select, branches, defaultBranch) {
  select.replaceChildren();
  branches.forEach((branch) => {
    const option = document.createElement('option');
    option.value = branch;
    option.textContent = branch;
    option.selected = branch === defaultBranch;
    select.append(option);
  });
  select.disabled = false;
}

export function renderDetection(root, detected) {
  const reqStr = window._('js.required') || 'required';
  setText(root.querySelector('[data-detection-framework]'), detected.framework || window._('python_app'));
  setText(root.querySelector('[data-detection-entrypoint]'),
    detected.entrypoints?.join(', ') || window._('js.no_entrypoint_found'));
  setText(root.querySelector('[data-detection-package]'), detected.package_manager || window._('js.unknown'));
  const names = detected.environment_keys || [];
  setText(root.querySelector('[data-detection-environment]'),
    names.length ? names.map((item) => item.required ? `${item.name} (${reqStr})` : item.name).join(', ') : window._('js.none_detected'));
  const evidence = detected.database_evidence || [];
  setText(root.querySelector('[data-detection-database]'), evidence.length ? evidence.join(', ') : window._('js.no_database_evidence'));
  const warning = root.querySelector('[data-detection-warnings]');
  setText(root.querySelector('[data-detection-warnings-text]'), (detected.warnings || []).join(' '));
  setHidden(warning, !(detected.warnings || []).length);
}

export function renderEnvironmentFields(container, keys) {
  const reqStr = window._('js.required') || 'required';
  container.replaceChildren();
  keys.filter((item) => item.name !== 'DATABASE_URL').forEach((item) => {
    const row = document.createElement('div');
    row.className = 'form-group';
    const label = document.createElement('label');
    const input = document.createElement('input');
    const id = `environment-${item.name.toLowerCase()}`;
    label.htmlFor = id;
    label.textContent = item.required ? `${item.name} (${reqStr})` : item.name;
    input.className = 'form-input form-input--code';
    input.dataset.environmentKey = item.name;
    input.id = id;
    input.type = 'password';
    input.autocomplete = 'off';
    input.required = Boolean(item.required);
    input.placeholder = window._('js.enter_value');
    row.append(label, input);
    container.append(row);
  });
}

export function environmentValues(root) {
  return Object.fromEntries([...root.querySelectorAll('[data-environment-key]')]
    .filter((input) => input.value)
    .map((input) => [input.dataset.environmentKey, input.value]));
}

export function renderDeploymentSteps(container, stage) {
  const stages = [
    ['source', window._('js.stage_source')], ['venv', window._('js.stage_venv')],
    ['dependencies', window._('js.stage_dependencies')], ['service', window._('js.stage_service')],
    ['nginx', window._('js.stage_nginx')], ['ssl', window._('js.stage_ssl')], ['complete', window._('js.stage_complete')],
  ];
  const current = Math.max(0, stages.findIndex(([name]) => name === stage));
  container.replaceChildren(...stages.map(([name, label], index) => {
    const row = document.createElement('div');
    const icon = document.createElement('div');
    const text = document.createElement('span');
    row.className = `scroll-step-item ${index < current ? 'completed' : index === current ? 'active' : 'pending'}`;
    icon.className = 'step-icon-wrap';
    text.className = 'step-text';
    text.textContent = label;
    if (index < current) icon.textContent = '✓';
    else if (index === current) icon.append(Object.assign(document.createElement('div'), { className: 'step-spinner' }));
    else icon.append(Object.assign(document.createElement('div'), { className: 'step-dot' }));
    row.append(icon, text);
    return row;
  }));
}
