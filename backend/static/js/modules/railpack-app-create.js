const form = document.querySelector('[data-railpack-builder]');

function toggleSource() {
  const image = form.querySelector('[data-source-type]').value === 'image';
  form.querySelector('[data-git-fields]').hidden = image;
  form.querySelector('[data-image-field]').hidden = !image;
}

function toggleDatabase() {
  const external = form.querySelector('#database_mode').value === 'external';
  form.querySelector('[data-database-url]').hidden = !external;
  form.querySelector('#database_url').required = external;
}

function databaseUi(types) {
  const primary = types[0] || '';
  const panel = form.querySelector('[data-panel-postgres]');
  const hint = form.querySelector('[data-database-detection]');
  const label = form.querySelector('[data-database-url-label]');
  const input = form.querySelector('[data-database-url-input]');
  panel.hidden = primary !== 'postgresql';
  if (primary !== 'postgresql' && form.querySelector('#database_mode').value === 'panel_postgres') form.querySelector('#database_mode').value = 'none';
  const examples = { postgresql: 'postgresql://user:password@host:5432/database', 'mariadb/mysql': 'mysql://user:password@host:3306/database', mongodb: 'mongodb://user:password@host:27017/database', redis: 'redis://:password@host:6379/0', sqlite: 'sqlite:////data/app.db' };
  label.textContent = primary ? `${primary} connection URL` : 'Connection URL';
  input.placeholder = examples[primary] || 'Enter a database connection URL';
  hint.textContent = types.length ? `Detected: ${types.join(', ')}. Choose the matching connection method; you can change it.` : 'No database detected. You can still choose one manually.';
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
    databaseUi(data.database_types || []);
    result.textContent = `${data.runtime} detected. Suggested port ${data.internal_port}; all suggestions remain editable.`;
  } catch (error) { result.textContent = error.message; }
}

if (form) {
  form.querySelector('[data-source-type]').addEventListener('change', toggleSource);
  form.querySelector('#database_mode').addEventListener('change', toggleDatabase);
  form.querySelector('[data-inspect]').addEventListener('click', inspect);
  toggleSource(); toggleDatabase(); databaseUi([]);
}
