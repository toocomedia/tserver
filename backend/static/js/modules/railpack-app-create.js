const form = document.querySelector('[data-railpack-builder]');

function sourceState() {
  const type = form.querySelector('[data-source-type]').value;
  const wordpress = type === 'wordpress';
  form.querySelector('[data-preset]').value = wordpress ? 'wordpress' : '';
  form.querySelector('[data-git-fields]').hidden = type !== 'git';
  form.querySelector('[data-image-field]').hidden = type !== 'image';
  form.querySelector('[data-wordpress-fields]').hidden = !wordpress;
  form.querySelector('#build_mode').closest('.form-group').hidden = wordpress;
  form.querySelector('#internal_port').closest('.form-group').hidden = wordpress;
  if (wordpress) form.querySelector('#internal_port').value = '80';
  wordpressDatabaseState(wordpress);
}

function wordpressDatabaseState(required) {
  const row = form.querySelector('[data-kind="mariadb"]');
  const enabled = row.querySelector('[data-database-enabled]');
  const provider = row.querySelector('[data-database-provider]');
  if (required) {
    enabled.checked = true;
    provider.value = 'docker';
  }
  row.dataset.sourceRequired = required ? 'true' : '';
  attachmentState(row);
}

function attachmentState(row) {
  const required = row.dataset.sourceRequired === 'true';
  const enabled = row.querySelector('[data-database-enabled]').checked;
  const external = row.querySelector('[data-database-provider]').value === 'external';
  const requirement = row.querySelector('[data-database-requirement]');
  row.querySelector('[data-database-enabled]').disabled = required;
  row.querySelector('[data-database-provider]').disabled = required || !enabled;
  requirement.hidden = !required;
  requirement.textContent = required ? 'Required by WordPress. The private MariaDB service is created with this app.' : '';
  const url = row.querySelector('[data-database-url]');
  url.hidden = !enabled || !external;
  url.required = enabled && external;
}

function addEnvironmentRow(key = '', value = '') {
  const list = form.querySelector('[data-environment-list]');
  const row = document.createElement('div');
  row.className = 'form-group';
  row.dataset.environmentRow = '';
  row.innerHTML = '<input class="form-input form-input--code" data-environment-key placeholder="VARIABLE_NAME" aria-label="Variable name"> <input class="form-input form-input--code" data-environment-value type="password" autocomplete="off" placeholder="Value" aria-label="Variable value"> <button class="btn btn--secondary" type="button" data-remove-environment>Remove</button>';
  row.querySelector('[data-environment-key]').value = key;
  row.querySelector('[data-environment-value]').value = value;
  row.querySelector('[data-remove-environment]').addEventListener('click', () => row.remove());
  list.append(row);
}

function environmentValues() {
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

function attachments() {
  return [...form.querySelectorAll('[data-database-row]')].flatMap((row) => {
    if (!row.querySelector('[data-database-enabled]').checked) return [];
    return [{
      kind: row.dataset.kind,
      provider: row.querySelector('[data-database-provider]').value,
      environment_key: row.querySelector('[data-database-key]').value,
      external_url: row.querySelector('[data-database-url]').value,
    }];
  });
}

async function inspect() {
  const result = form.querySelector('[data-inspect-result]');
  const body = new FormData();
  body.set('repository_url', form.querySelector('[data-repository-url]').value);
  body.set('branch', form.querySelector('[data-branch]').value || 'main');
  try {
    result.textContent = 'Inspecting repository…';
    const response = await fetch('/plugins/railpack_apps/inspect', { method: 'POST', headers: { 'X-CSRF-Token': document.querySelector('[name="csrf_token"]').value }, body });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Inspection failed.');
    form.querySelector('[data-repository-url]').value = data.repository_url;
    form.querySelector('[data-branch]').value = data.branch;
    form.querySelector('#build_mode').value = data.build_mode;
    form.querySelector('#internal_port').value = data.internal_port;
    const types = data.database_types || [];
    form.querySelector('[data-database-detection]').textContent = types.length ? `Detected: ${types.join(', ')}. Review the selected services.` : 'No database detected. You can still choose services manually.';
    types.forEach((kind) => { const row = form.querySelector(`[data-kind="${kind === 'mariadb/mysql' ? 'mariadb' : kind}"]`); if (row) { row.querySelector('[data-database-enabled]').checked = true; attachmentState(row); } });
    result.textContent = `${data.runtime} detected. Suggested port ${data.internal_port}; all suggestions remain editable.`;
  } catch (error) { result.textContent = error.message; }
}

if (form) {
  form.querySelector('[data-source-type]').addEventListener('change', sourceState);
  form.querySelector('[data-inspect]').addEventListener('click', inspect);
  form.querySelector('[data-add-environment]').addEventListener('click', () => addEnvironmentRow());
  form.querySelectorAll('[data-database-row]').forEach((row) => row.addEventListener('change', () => attachmentState(row)));
  form.addEventListener('submit', (event) => {
    const error = form.querySelector('[data-environment-error]');
    try {
      form.querySelector('[data-environment-values]').value = JSON.stringify(environmentValues());
      form.querySelector('[data-database-attachments]').value = JSON.stringify(attachments());
      error.hidden = true;
    } catch (reason) { event.preventDefault(); error.textContent = reason.message; error.hidden = false; }
  });
  form.querySelectorAll('[data-database-row]').forEach(attachmentState);
  sourceState();
}
