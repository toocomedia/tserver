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
  setText(root.querySelector('[data-detection-framework]'), detected.framework || 'Python app');
  setText(root.querySelector('[data-detection-entrypoint]'),
    detected.entrypoints?.join(', ') || 'No supported entrypoint found');
  setText(root.querySelector('[data-detection-package]'), detected.package_manager || 'Unknown');
  const names = detected.environment_keys || [];
  setText(root.querySelector('[data-detection-environment]'),
    names.length ? names.map((item) => item.required ? `${item.name} (required)` : item.name).join(', ') : 'None detected');
  const evidence = detected.database_evidence || [];
  setText(root.querySelector('[data-detection-database]'), evidence.length ? evidence.join(', ') : 'No database evidence');
  const warning = root.querySelector('[data-detection-warnings]');
  setText(root.querySelector('[data-detection-warnings-text]'), (detected.warnings || []).join(' '));
  setHidden(warning, !(detected.warnings || []).length);
}

export function renderEnvironmentFields(container, keys) {
  container.replaceChildren();
  keys.filter((item) => item.name !== 'DATABASE_URL').forEach((item) => {
    const row = document.createElement('div');
    row.className = 'form-group';
    const label = document.createElement('label');
    const input = document.createElement('input');
    const id = `environment-${item.name.toLowerCase()}`;
    label.htmlFor = id;
    label.textContent = item.required ? `${item.name} (required)` : item.name;
    input.className = 'form-input form-input--code';
    input.dataset.environmentKey = item.name;
    input.id = id;
    input.type = 'password';
    input.autocomplete = 'off';
    input.required = Boolean(item.required);
    input.placeholder = 'Enter value';
    row.append(label, input);
    container.append(row);
  });
}

export function environmentValues(root) {
  return Object.fromEntries([...root.querySelectorAll('[data-environment-key]')]
    .filter((input) => input.value)
    .map((input) => [input.dataset.environmentKey, input.value]));
}
