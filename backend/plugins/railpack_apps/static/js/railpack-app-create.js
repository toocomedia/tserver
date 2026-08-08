import { addEnvironmentRow, csrfHeaders, environmentValues, fetchJson, inspectRegistryImage, renderDeployment, renderInspection, setHidden, setText, initDropdowns } from './railpack-app-create-ui.js';
const form = document.querySelector('[data-railpack-builder]');
if (form) {
  const state = { step: 1, unlocked: 1, appId: null, deploymentId: null };
  const query = (selector) => form.querySelector(selector);
  const panel = (step) => query(`[data-wizard-panel="${step}"]`);
  function renderStep(step) {
    state.step = step;
    for (let index = 1; index <= 5; index += 1) {
      setHidden(panel(index), index !== step);
      const nav = query(`[data-wizard-nav="${index}"]`);
      nav.classList.toggle('active', index === step);
      nav.classList.toggle('disabled', index > state.unlocked);
    }
    setHidden(query('[data-wizard-back]'), step === 1 || step >= 4);
    setHidden(query('[data-wizard-cancel]'), step >= 4);
    setHidden(query('[data-wizard-next]'), step >= 4);
    setText(query('[data-wizard-hint]'), `Step ${step} of 5: ${['Source', 'Inspection', 'Configuration', 'Install', 'Result'][step - 1]}`);
    setText(query('[data-wizard-step-title]'), `${step}. ${['Source', 'Inspection', 'Configuration', 'Install', 'Result'][step - 1]}`);
    if (step === 1) setText(query('[data-wizard-next]'), query('[data-source-type]').value === 'git' ? 'Inspect repository' : 'Review configuration');
    if (step === 2) setText(query('[data-wizard-next]'), 'Continue to configuration');
    if (step === 3) setText(query('[data-wizard-next]'), 'Deploy app');
  }
  function sourceState() {
    const type = query('[data-source-type]').value;
    const wordpress = type === 'wordpress';
    state.unlocked = 1;
    setHidden(query('[data-git-fields]'), type !== 'git');
    setHidden(query('[data-image-field]'), type !== 'image');
    setHidden(query('[data-wordpress-fields]'), !wordpress);
    setHidden(query('[data-build-mode-group]'), wordpress);
    setHidden(query('[data-port-group]'), wordpress);
    toggleSourceInputs(type);
    query('[data-preset]').value = wordpress ? 'wordpress' : '';
    if (wordpress) query('#internal_port').value = '80';
    wordpressDatabaseState(wordpress);
    domainState();
    renderStep(1);
  }
  function domainState() {
    const selected = query('[data-domain-select]');
    const ssl = query('[data-ssl-request]');
    const hasSsl = selected?.dataset.domainSsl === 'true';
    ssl.checked = false;
    ssl.disabled = hasSsl;
    setText(query('[data-ssl-hint]'), hasSsl ? 'HTTPS is already active for this domain. The existing certificate will be used.' : 'No certificate is attached to this domain. Select this option to issue HTTPS after deployment.');
  }
  function toggleSourceInputs(type) {
    const git = type === 'git';
    query('[data-repository-url]').disabled = !git;
    query('[data-repository-url]').required = git;
    query('[data-branch]').disabled = !git;
    query('[data-image-reference]').disabled = type !== 'image';
    query('[data-image-reference]').required = type === 'image';
    form.querySelectorAll('[data-wordpress-fields] input').forEach((input) => { input.disabled = type !== 'wordpress'; input.required = type === 'wordpress'; });
  }
  function wordpressDatabaseState(required) {
    const row = query('[data-kind="mariadb"]');
    if (required) {
      row.querySelector('[data-database-enabled]').checked = true;
      // provider may be in the external options panel
      const providerEl = _dbField(row, '[data-database-provider]');
      if (providerEl) providerEl.value = 'docker';
    }
    row.dataset.sourceRequired = required ? 'true' : '';
    attachmentState(row);
  }
  function _dbField(row, selector) {
    // First check inside the card row itself, then in the sibling options panel
    const inRow = row.querySelector(selector);
    if (inRow) return inRow;
    const kind = row.dataset.kind;
    if (kind) {
      const panel = form.querySelector(`[data-database-options][data-database-parent="${kind}"]`);
      if (panel) return panel.querySelector(selector);
    }
    return null;
  }
  function _dbPanel(row) {
    const kind = row.dataset.kind;
    return kind ? form.querySelector(`[data-database-options][data-database-parent="${kind}"]`) : row.querySelector('[data-database-options]');
  }
  function attachmentState(row) {
    const required = row.dataset.sourceRequired === 'true';
    const enabled = row.querySelector('[data-database-enabled]').checked;
    const provider = _dbField(row, '[data-database-provider]');
    const optionsPanel = _dbPanel(row);
    if (optionsPanel) optionsPanel.hidden = !enabled;
    row.classList.toggle('settings-choice--active', enabled);
    row.querySelector('[data-database-enabled]').disabled = required;
    if (provider) provider.disabled = required || !enabled;
    const providerVal = provider ? provider.value : 'docker';
    const url = _dbField(row, '[data-database-url]');
    const external = providerVal === 'external';
    const supabase = providerVal === 'supabase';
    const externalEl = _dbField(row, '[data-database-external]');
    if (externalEl) externalEl.hidden = !enabled || !external;
    if (url) url.required = enabled && external;
    const supabasePicker = _dbField(row, '[data-database-supabase-picker]');
    if (supabasePicker) supabasePicker.hidden = !enabled || !supabase;
    const supabaseSelect = _dbField(row, '[data-database-supabase-project]');
    if (supabaseSelect) supabaseSelect.required = enabled && supabase;
    const reqEl = _dbField(row, '[data-database-requirement]');
    setHidden(reqEl, !required);
    setText(reqEl, required ? 'Required by WordPress. The private MariaDB service is created with this app.' : '');
  }

  function applyInspection(data) {
    if (['railpack', 'dockerfile'].includes(data.build_mode)) query('#build_mode').value = data.build_mode;
    const port = Number(data.internal_port);
    if (Number.isInteger(port) && port > 0 && port <= 65535) query('#internal_port').value = port;
    (data.database_types || []).forEach((kind) => {
      const normalized = kind === 'mariadb/mysql' ? 'mariadb' : kind;
      const row = query(`[data-database-row][data-kind="${normalized}"]`);
      if (row) { row.querySelector('[data-database-enabled]').checked = true; attachmentState(row); }
    });
    setText(query('[data-database-detection]'), data.database_types?.length ? `Detected: ${data.database_types.join(', ')}. Review the selected services.` : 'No database detected. You can still choose services manually.');
    renderInspection(form, data);
  }
  async function inspectSource() {
    const type = query('[data-source-type]').value;
    if (type !== 'git') return showNonGitInspection(type);
    if (!query('[data-repository-url]').reportValidity()) return;
    setHidden(query('[data-inspect-error]'), true);
    setHidden(query('[data-inspect-results]'), true);
    setHidden(query('[data-inspect-loading]'), false);
    
    const nextBtn = query('[data-wizard-next]');
    const originalText = nextBtn.textContent;
    nextBtn.disabled = true;
    nextBtn.innerHTML = '<span class="step-spinner" style="width: 14px; height: 14px; border-width: 2px; margin-right: 8px;"></span>Inspecting...';

    try {
      const body = new FormData();
      body.set('repository_url', query('[data-repository-url]').value);
      body.set('branch', query('[data-branch]').value || 'main');
      const data = await fetchJson('/plugins/railpack_apps/inspect', { method: 'POST', headers: csrfHeaders(), body });
      query('[data-repository-url]').value = data.repository_url;
      query('[data-branch]').value = data.branch;
      applyInspection(data);
      state.unlocked = 2;
      renderStep(2);
      setHidden(query('[data-inspect-results]'), false);
    } catch (error) { 
      showInspectionError(error); 
    } finally { 
      setHidden(query('[data-inspect-loading]'), true);
      nextBtn.disabled = false;
      nextBtn.textContent = originalText;
    }
  }
  async function showNonGitInspection(type) {
    const image = type === 'image';
    if (image && !query('[data-image-reference]').reportValidity()) return;
    if (image) return inspectImageSource();
    finishNonGitInspection({ runtime: 'WordPress preset', build_mode: 'image', internal_port: 80, database_types: ['mariadb'], summary: 'WordPress will install with its private MariaDB service.' });
  }
  async function inspectImageSource() {
    setHidden(query('[data-inspect-error]'), true);
    setHidden(query('[data-inspect-results]'), true);
    setHidden(query('[data-inspect-loading]'), false);
    try { finishNonGitInspection(await inspectRegistryImage(query('[data-image-reference]').value)); }
    catch (error) { showInspectionError(error); }
    finally { setHidden(query('[data-inspect-loading]'), true); }
  }
  function finishNonGitInspection(data) {
    applyInspection(data);
    state.unlocked = 2;
    renderStep(2);
    setHidden(query('[data-inspect-results]'), false);
  }

  function showInspectionError(error) {
    setText(query('[data-inspect-error-text]'), error.message || 'Inspection failed.');
    setHidden(query('[data-inspect-error]'), false);
  }

  function submitValues() {
    const error = query('[data-environment-error]');
    try {
      query('[data-environment-values]').value = JSON.stringify(environmentValues(form));
      query('[data-database-attachments]').value = JSON.stringify(attachments());
      setHidden(error, true);
      return true;
    } catch (reason) { setText(error, reason.message); setHidden(error, false); return false; }
  }

  function attachments() {
    return [...form.querySelectorAll('[data-database-row]')].flatMap((row) => {
      if (!row.querySelector('[data-database-enabled]').checked) return [];
      const provider = row.querySelector('[data-database-provider]').value;
      const supabasePicker = row.querySelector('[data-database-supabase-project]');
      const supabase_project_id = supabasePicker ? supabasePicker.value : '';
      if (provider === 'supabase' && !supabase_project_id) {
        throw new Error('Select a Supabase project for the PostgreSQL attachment.');
      }
      return [{ kind: row.dataset.kind, provider, environment_key: row.querySelector('[data-database-key]').value, external_url: row.querySelector('[data-database-url]') ? row.querySelector('[data-database-url]').value : '', supabase_project_id }];
    });
  }

  async function startDeployment() {
    if (!submitValues() || !form.reportValidity()) return;
    const nextBtn = query('[data-wizard-next]');
    const originalText = nextBtn.textContent;
    nextBtn.disabled = true;
    nextBtn.innerHTML = '<span class="step-spinner" style="width: 14px; height: 14px; border-width: 2px; margin-right: 8px;"></span>Deploying...';
    try {
      const data = await fetchJson(form.action, { method: 'POST', headers: { ...csrfHeaders(), Accept: 'application/json' }, body: new FormData(form) });
      state.appId = data.app_id;
      state.deploymentId = data.deployment_id;
      state.unlocked = 4;
      renderStep(4);
      pollDeployment();
    } catch (error) { 
      setText(query('[data-environment-error]'), error.message); 
      setHidden(query('[data-environment-error]'), false); 
    } finally {
      nextBtn.disabled = false;
      nextBtn.textContent = originalText;
    }
  }

  async function pollDeployment() {
    try {
      const data = await fetchJson(`/plugins/railpack_apps/${state.appId}/deployments/${state.deploymentId}`, { headers: csrfHeaders() });
      renderDeployment(form, data);
      if (['queued', 'running'].includes(data.status)) return window.setTimeout(pollDeployment, 1200);
      finishDeployment(data);
    } catch (error) { finishDeployment({ status: 'failed', stage: 'complete', error: error.message }); }
  }

  function finishDeployment(data) {
    state.unlocked = 5;
    setText(query('[data-result-state]'), data.status === 'success' ? 'Complete' : 'Failed');
    setText(query('[data-result-summary]'), data.status === 'success' ? 'Deployment completed successfully.' : 'Deployment failed. Review the output and manage the app for recovery.');
    setText(query('[data-deployment-error-text]'), data.error || 'Deployment failed.');
    setHidden(query('[data-deployment-error]'), data.status === 'success');
    const url = `/plugins/railpack_apps/${state.appId}`;
    query('[data-deployment-dashboard]').href = url;
    query('[data-deployment-details]').href = url;
    setHidden(query(data.status === 'success' ? '[data-deployment-dashboard]' : '[data-deployment-details]'), false);
    renderStep(5);
  }

  query('[data-source-type]').addEventListener('change', sourceState);
  query('[data-domain-select]').addEventListener('change', () => { domainState(); });
  query('[data-inspect-retry]').addEventListener('click', inspectSource);
  query('[data-add-environment]').addEventListener('click', () => addEnvironmentRow(form));
  form.querySelectorAll('[data-database-row]').forEach((row) => {
    row.querySelector('[data-database-enabled]').addEventListener('change', () => attachmentState(row));
    const providerEl = _dbField(row, '[data-database-provider]');
    if (providerEl) providerEl.addEventListener('change', () => attachmentState(row));
  });
  query('[data-wizard-next]').addEventListener('click', () => [inspectSource, () => { state.unlocked = 3; renderStep(3); }, startDeployment][state.step - 1]?.());
  query('[data-wizard-back]').addEventListener('click', () => renderStep(Math.max(1, state.step - 1)));
  form.addEventListener('submit', (event) => { event.preventDefault(); if (state.step === 3) startDeployment(); });
  form.querySelectorAll('[data-wizard-nav]').forEach((item) => item.addEventListener('click', () => { const step = Number(item.dataset.wizardNav); if (step <= state.unlocked) renderStep(step); }));
  form.querySelectorAll('[data-database-row]').forEach(attachmentState);
  initDropdowns();

  const repoInput = query('[data-repository-url]');
  if (repoInput) {
    repoInput.addEventListener('blur', async () => {
      if (!repoInput.value) return;
      const branchSpinner = query('[data-branch-spinner]');
      const branchMenu = query('[data-branch-menu]');
      if (branchSpinner) branchSpinner.style.display = 'inline-block';
      try {
        const body = new FormData();
        body.set('repository_url', repoInput.value);
        const data = await fetchJson('/plugins/railpack_apps/inspect-branches', { method: 'POST', headers: csrfHeaders(), body });
        if (branchMenu) {
            branchMenu.innerHTML = '';
            data.branches.forEach((branch) => {
                const div = document.createElement('div');
                div.className = `custom-dropdown__item ${branch === data.default_branch ? 'is-selected' : ''}`;
                div.dataset.dropdownItem = '';
                div.dataset.value = branch;
                div.dataset.label = branch;
                div.textContent = branch;
                branchMenu.appendChild(div);
            });
            const branchInput = query('#branch');
            const branchLabel = branchMenu.closest('.custom-dropdown').querySelector('[data-dropdown-label]');
            if (branchInput) {
                branchInput.value = data.default_branch;
                branchInput.dataset.value = data.default_branch;
            }
            if (branchLabel) branchLabel.textContent = data.default_branch;
        }
      } catch (error) {
         console.warn("Could not fetch branches:", error);
      } finally {
        if (branchSpinner) branchSpinner.style.display = 'none';
      }
    });
  }
  sourceState();
}
