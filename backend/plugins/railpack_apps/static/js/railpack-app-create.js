import { addEnvironmentRow, addStorageMountRow, storageMountValues, csrfHeaders, environmentValues, fetchJson, inspectRegistryImage, renderDeployment, renderInspection, setHidden, setText, initDropdowns, parseAndApplyBulkEnv } from './railpack-app-create-ui.js';
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

    const stepDescriptions = [
      'Select Application Source',
      'Review Inspection Summary',
      'Configure Environment & Services',
      'Installing & Deploying',
      'Deployment Results'
    ];
    setText(query('[data-wizard-step-title]'), stepDescriptions[step - 1]);
    if (step === 1) setText(query('[data-wizard-next]'), query('[data-source-type]').value === 'git' ? 'Inspect repository' : 'Review configuration');
    if (step === 2) setText(query('[data-wizard-next]'), 'Continue to configuration');
    if (step === 3) setText(query('[data-wizard-next]'), 'Deploy app');
    setTimeout(() => {
      const scrollContainer = query('.wizard-content-area');
      if (scrollContainer && scrollContainer.updateScrollMask) scrollContainer.updateScrollMask();
    }, 10);
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
    updateRefType();
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
      const providerEl = _dbField(row, '[data-database-provider]');
      if (providerEl) providerEl.value = 'docker';
    }
    row.dataset.sourceRequired = required ? 'true' : '';
    attachmentState(row);
  }
  function _dbField(row, selector) {
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

  function updateRefType() {
    const refTypeInput = query('[data-git-ref-type]');
    const branchGroup = query('[data-git-branch-group]');
    const customRefGroup = query('[data-git-custom-ref-group]');
    const customRefLabel = query('[data-git-custom-ref-label]');
    const customRefInput = query('[data-git-custom-ref]');
    const gitRefHidden = query('[data-git-ref]');
    const type = refTypeInput?.value || 'branch';
    const isBranch = type === 'branch';
    if (branchGroup) setHidden(branchGroup, !isBranch);
    if (customRefGroup) setHidden(customRefGroup, isBranch);
    if (customRefLabel) setText(customRefLabel, type === 'tag' ? 'Tag Name' : 'Commit SHA');
    if (isBranch) {
      if (gitRefHidden) gitRefHidden.value = query('#branch')?.value || 'main';
    } else {
      if (gitRefHidden) gitRefHidden.value = customRefInput?.value || '';
    }
  }

  function updateBuildModeFields() {
    const buildModeInput = query('#build_mode');
    const dockerfilePathGroup = query('[data-dockerfile-path-group]');
    const buildArgsGroup = query('[data-build-args-group]');
    const mode = buildModeInput?.value || 'railpack';
    const isDockerfile = mode === 'dockerfile';
    if (dockerfilePathGroup) setHidden(dockerfilePathGroup, !isDockerfile);
    if (buildArgsGroup) setHidden(buildArgsGroup, !isDockerfile);
  }

  function applyInspection(data) {
    const targetMode = data.build_mode || 'railpack';
    if (query('#build_mode')) {
      query('#build_mode').value = targetMode;
      updateBuildModeFields();
    }
    const port = Number(data.internal_port);
    if (Number.isInteger(port) && port > 0 && port <= 65535) query('#internal_port').value = port;
    (data.database_types || []).forEach((kind) => {
      const normalized = kind === 'mariadb/mysql' ? 'mariadb' : kind;
      const row = query(`[data-database-row][data-kind="${normalized}"]`);
      if (row) { row.querySelector('[data-database-enabled]').checked = true; attachmentState(row); }
    });
    setText(query('[data-database-detection]'), data.database_types?.length ? `Detected: ${data.database_types.join(', ')}. Review the selected services.` : 'No database detected. You can still choose services manually.');
    renderInspection(form, data);
    updateBuildModeFields();
  }
  async function inspectSource() {
    const domainInput = query('[data-domain-select]');
    const sourceError = query('[data-source-error]');
    if (!domainInput.value) {
      if (sourceError) { setText(sourceError, 'Please select a Target Domain to continue.'); setHidden(sourceError, false); }
      return;
    }
    if (sourceError) setHidden(sourceError, true);

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
      body.set('draft_key_id', query('[data-draft-key-id]')?.value || '');
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
    const errorEl = query('[data-source-error]');
    if (errorEl) {
      setText(errorEl, error.message || 'Inspection failed.');
      setHidden(errorEl, false);
    }
  }

  function submitValues() {
    const error = query('[data-environment-error]');
    try {
      updateRefType();
      query('[data-environment-values]').value = JSON.stringify(environmentValues(form));
      query('[data-database-attachments]').value = JSON.stringify(attachments());
      query('[data-storage-mounts]').value = JSON.stringify(storageMountValues(form));
      setHidden(error, true);
      return true;
    } catch (reason) { setText(error, reason.message); setHidden(error, false); return false; }
  }

  function attachments() {
    return [...form.querySelectorAll('[data-database-row]')].flatMap((row) => {
      if (!row.querySelector('[data-database-enabled]').checked) return [];
      const providerEl = _dbField(row, '[data-database-provider]');
      const provider = providerEl ? providerEl.value : 'docker';
      const supabasePicker = _dbField(row, '[data-database-supabase-project]');
      const supabase_project_id = supabasePicker ? supabasePicker.value : '';
      if (provider === 'supabase' && !supabase_project_id) {
        throw new Error('Select a Supabase project for the PostgreSQL attachment.');
      }
      const urlEl = _dbField(row, '[data-database-url]');
      return [{ kind: row.dataset.kind, provider, environment_key: row.querySelector('[data-database-key]').value, external_url: urlEl ? urlEl.value : '', supabase_project_id }];
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
  query('[data-git-ref-type]')?.addEventListener('change', updateRefType);
  query('[data-git-custom-ref]')?.addEventListener('input', updateRefType);
  query('#build_mode')?.addEventListener('change', updateBuildModeFields);

  query('[data-add-environment]').addEventListener('click', () => addEnvironmentRow(form));
  query('[data-add-storage-mount]')?.addEventListener('click', () => addStorageMountRow(form));

  const generateDeployKeyBtn = query('[data-generate-deploy-key]');
  const deployKeyDisplay = query('[data-deploy-key-display]');
  const deployKeyPublicText = query('[data-deploy-key-public]');
  const draftKeyIdInput = query('[data-draft-key-id]');
  const copyDeployKeyBtn = query('[data-copy-deploy-key]');

  if (generateDeployKeyBtn) {
    generateDeployKeyBtn.addEventListener('click', async () => {
      generateDeployKeyBtn.disabled = true;
      generateDeployKeyBtn.textContent = 'Generating...';
      try {
        const data = await fetchJson('/plugins/railpack_apps/draft-deploy-key', {
          method: 'POST',
          headers: csrfHeaders(),
        });
        if (draftKeyIdInput) draftKeyIdInput.value = data.draft_id;
        if (deployKeyPublicText) deployKeyPublicText.value = data.public_key;
        if (deployKeyDisplay) setHidden(deployKeyDisplay, false);
        generateDeployKeyBtn.textContent = 'Regenerate Key';
      } catch (err) {
        alert('Failed to generate deploy key: ' + err.message);
        generateDeployKeyBtn.textContent = 'Generate Deploy Key';
      } finally {
        generateDeployKeyBtn.disabled = false;
      }
    });
  }

  if (copyDeployKeyBtn && deployKeyPublicText) {
    copyDeployKeyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(deployKeyPublicText.value);
      copyDeployKeyBtn.textContent = 'Copied!';
      setTimeout(() => { copyDeployKeyBtn.textContent = 'Copy Key'; }, 2000);
    });
  }

  const toggleAdvBtn = query('[data-toggle-advanced-build]');
  const advPanel = query('[data-advanced-build-panel]');
  if (toggleAdvBtn && advPanel) {
    toggleAdvBtn.addEventListener('click', () => {
      const isHidden = advPanel.hidden || advPanel.classList.contains('hidden') || advPanel.style.display === 'none';
      setHidden(advPanel, !isHidden);
      toggleAdvBtn.textContent = isHidden ? '⚙ Hide Advanced Options' : '⚙ Advanced Options';
    });
  }

  const toggleBulkBtn = query('[data-toggle-bulk-env]');
  const bulkPanel = query('[data-bulk-env-panel]');
  const bulkInput = query('[data-bulk-env-input]');
  const applyBulkBtn = query('[data-apply-bulk-env]');
  const cancelBulkBtn = query('[data-cancel-bulk-env]');

  if (toggleBulkBtn && bulkPanel) {
    toggleBulkBtn.addEventListener('click', () => {
      const isHidden = bulkPanel.hidden || bulkPanel.classList.contains('hidden') || bulkPanel.style.display === 'none';
      setHidden(bulkPanel, !isHidden);
      if (isHidden && bulkInput) bulkInput.focus();
    });
  }
  if (cancelBulkBtn && bulkPanel) {
    cancelBulkBtn.addEventListener('click', () => {
      setHidden(bulkPanel, true);
      if (bulkInput) bulkInput.value = '';
    });
  }
  if (applyBulkBtn && bulkPanel && bulkInput) {
    applyBulkBtn.addEventListener('click', () => {
      parseAndApplyBulkEnv(form, bulkInput.value);
      setHidden(bulkPanel, true);
      bulkInput.value = '';
    });
  }

  const addEnvBtn = query('[data-add-environment]');
  if (addEnvBtn) {
    addEnvBtn.addEventListener('click', () => addEnvironmentRow(form));
  }
  const addMountBtn = query('[data-add-storage-mount]');
  if (addMountBtn) {
    addMountBtn.addEventListener('click', () => addStorageMountRow(form));
  }

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
        body.set('draft_key_id', query('[data-draft-key-id]')?.value || '');
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
  const scrollContainer = query('.wizard-content-area');
  const scrollWrapper = scrollContainer?.closest('.wizard-scroll-wrapper');
  if (scrollContainer && scrollWrapper) {
    scrollContainer.updateScrollMask = () => {
      const isScrollable = scrollContainer.scrollHeight > scrollContainer.clientHeight + 10;
      const atTop = scrollContainer.scrollTop <= 2;
      const atBottom = Math.ceil(scrollContainer.scrollTop + scrollContainer.clientHeight) >= scrollContainer.scrollHeight - 4;
      scrollWrapper.classList.toggle('can-scroll-top', isScrollable && !atTop);
      scrollWrapper.classList.toggle('can-scroll-bottom', isScrollable && !atBottom);
    };
    scrollContainer.addEventListener('scroll', scrollContainer.updateScrollMask, { passive: true });
    
    // Use ResizeObserver to reliably detect layout changes and hide phantom arrows
    const resizeObserver = new ResizeObserver(() => {
      if (scrollContainer && scrollContainer.updateScrollMask) scrollContainer.updateScrollMask();
    });
    if (scrollContainer.firstElementChild) {
      resizeObserver.observe(scrollContainer.firstElementChild);
    }
    resizeObserver.observe(scrollContainer);
    window.addEventListener('resize', scrollContainer.updateScrollMask, { passive: true });
    
    scrollWrapper.querySelectorAll('.scroll-arrow').forEach(btn => {
      btn.addEventListener('click', () => {
        const dir = parseInt(btn.dataset.scrollDir, 10);
        scrollContainer.scrollBy({ top: dir * 150, behavior: 'smooth' });
      });
    });

    scrollContainer.updateScrollMask();
  }
}
