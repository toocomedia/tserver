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
}

function attachmentState(row) {
  const enabled = row.querySelector('[data-database-enabled]').checked;
  const external = row.querySelector('[data-database-provider]').value === 'external';
  row.querySelector('[data-database-provider]').disabled = !enabled;
  const url = row.querySelector('[data-database-url]');
  url.hidden = !enabled || !external;
  url.required = enabled && external;
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
  form.querySelectorAll('[data-database-row]').forEach((row) => row.addEventListener('change', () => attachmentState(row)));
  form.addEventListener('submit', () => { form.querySelector('[data-database-attachments]').value = JSON.stringify(attachments()); });
  form.querySelectorAll('[data-database-row]').forEach(attachmentState);
  sourceState();
}
