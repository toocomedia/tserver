import { addEnvironmentRow, addStorageMountRow, storageMountValues, csrfHeaders, environmentValues, fetchJson, inspectRegistryImage, renderDeployment, renderInspection, setHidden, setText, initDropdowns, parseAndApplyBulkEnv } from './railpack-app-create-ui.js';
const form = document.querySelector('[data-railpack-builder]');
if (form) {
  const state = { step: 1, unlocked: 1, appId: null, deploymentId: null };
  const appliedPlanIds = new Set();
  const query = (selector) => form.querySelector(selector);
  const queryAll = (selector) => form.querySelectorAll(selector);
  const panel = (step) => query(`[data-wizard-panel="${step}"]`);
  function renderStep(step) {
    state.step = step;
    const opticEl = form.closest('.apps-engine-optic');
    if (opticEl) {
      opticEl.classList.remove('is-step-1', 'is-step-2', 'is-step-3', 'is-step-4', 'is-step-5');
      opticEl.classList.add(`is-step-${step}`);
    }
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
    if (step === 3) setText(query('[data-wizard-next]'), query('#deploy_type')?.value === 'official_stack' ? 'Deploy stack' : 'Deploy app');
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
    if (!submitValues() || !form.reportValidity()) {
      const errText = query('[data-environment-error]')?.textContent || 'Validation error: please check required fields.';
      throw new Error(errText);
    }
    const nextBtn = query('[data-wizard-next]');
    const originalText = nextBtn ? nextBtn.textContent : '';
    if (nextBtn) {
      nextBtn.disabled = true;
      nextBtn.innerHTML = '<span class="step-spinner" style="width: 14px; height: 14px; border-width: 2px; margin-right: 8px;"></span>Deploying...';
    }
    try {
      const data = await fetchJson(form.action, { method: 'POST', headers: { ...csrfHeaders(), Accept: 'application/json' }, body: new FormData(form) });
      state.appId = data.app_id;
      state.deploymentId = data.deployment_id;
      state.unlocked = 4;
      renderStep(4);
      pollDeployment();
      return { success: true, app_id: data.app_id, deployment_id: data.deployment_id };
    } catch (error) { 
      setText(query('[data-environment-error]'), error.message); 
      setHidden(query('[data-environment-error]'), false); 
      throw error;
    } finally {
      if (nextBtn) {
        nextBtn.disabled = false;
        nextBtn.textContent = originalText;
      }
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
    const isSuccess = data.status === 'success';
    setText(query('[data-result-state]'), isSuccess ? 'Complete' : 'Failed');
    setText(query('[data-result-summary]'), isSuccess ? 'Deployment completed successfully.' : 'Deployment failed. Review the output or diagnose and fix with AI.');
    setText(query('[data-deployment-error-text]'), data.error || 'Deployment failed.');
    setHidden(query('[data-deployment-error]'), isSuccess);
    setHidden(query('[data-result-failure-actions]'), isSuccess);
    const url = `/plugins/railpack_apps/${state.appId}`;
    query('[data-deployment-dashboard]').href = url;
    query('[data-deployment-details]').href = url;
    setHidden(query(isSuccess ? '[data-deployment-dashboard]' : '[data-deployment-details]'), false);
    renderStep(5);

    // Auto-prompt AI if drawer is open during failure
    if (!isSuccess && window.AiHelper && typeof window.AiHelper.isOpen === "function" && window.AiHelper.isOpen()) {
      const errSnippet = data.error ? `Error: ${data.error}` : "Build/Deployment failed.";
      const logSnippet = (data.output || "").slice(-2000);
      const prompt = `The application build or deployment failed with:\n${errSnippet}\n\nRecent build logs:\n\`\`\`log\n${logSnippet}\n\`\`\`\nDiagnose root cause and create a review-only configuration draft if justified. Do not deploy or expose secret values.`;
      if (typeof window.AiHelper.sendMessage === "function") {
        window.AiHelper.sendMessage(prompt);
      }
    }
  }

  query('[data-wizard-retry]')?.addEventListener('click', () => {
    startDeployment();
  });

  query('[data-wizard-edit-config]')?.addEventListener('click', () => {
    state.unlocked = Math.max(state.unlocked, 3);
    renderStep(3);
  });

  query('[data-ai-diagnose-error]')?.addEventListener('click', () => {
    if (!window.AiHelper) return;
    const output = query('[data-deployment-output]')?.textContent || '';
    const errText = query('[data-deployment-error-text]')?.textContent || 'Deployment failed';
    const logSnippet = output.slice(-2000);
    const appCtx = state.appId ? ` for App #${state.appId}` : '';
    const prompt = `The application build or deployment failed${appCtx} with:\nError: ${errText}\n\nRecent build logs:\n\`\`\`log\n${logSnippet}\n\`\`\`\nDiagnose root cause and create a review-only configuration draft if justified. Do not deploy or expose secret values.`;
    window.AiHelper.open({
      split: true,
      taskType: "app_redeploy",
      context: `App Deployment Failure #${state.deploymentId || state.appId || ""}`,
      initialPrompt: prompt,
    });
  });

  queryAll('[data-source-card]').forEach((card) => {
    card.addEventListener('click', () => {
      queryAll('[data-source-card]').forEach((c) => c.classList.remove('selected'));
      card.classList.add('selected');
      const srcType = card.dataset.sourceCard;
      const srcInput = query('[data-source-type]');
      if (srcInput && srcInput.value !== srcType) {
        srcInput.value = srcType;
        sourceState();
      }
    });
  });

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

  // -------------------------------------------------------------
  // AI Wizard Flow & Action Plan Integration
  // -------------------------------------------------------------
  window.applyAiAppPlan = function (planData, options = {}) {
    if (!planData) return;
    const p = planData.payload || planData;
    const isStack = planData.action_type === "official_stack_install" || p.deploy_type === "official_stack" || !!p.stack_catalog_id;

    if (isStack) {
      const depTypeInput = query("#deploy_type") || query("[data-deploy-type]");
      if (depTypeInput) depTypeInput.value = "official_stack";
      const catInput = query("#stack_catalog_id") || query("[data-stack-catalog-id]");
      if (catInput) catInput.value = p.stack_catalog_id || "";
      const verInput = query("#stack_version") || query("[data-stack-version]");
      if (verInput) verInput.value = p.stack_version || "";
      const settingsInput = query("#nonsecret_settings") || query("[data-nonsecret-settings]");
      if (settingsInput) settingsInput.value = JSON.stringify(p.nonsecret_settings || {});

      const stackPanel = query("[data-stack-configuration-panel]");
      if (stackPanel) {
        stackPanel.style.display = "block";
        const titleEl = stackPanel.querySelector("[data-stack-title]");
        if (titleEl && p.stack_display_name) titleEl.textContent = p.stack_display_name;
        const badgeEl = stackPanel.querySelector("[data-stack-version-badge]");
        if (badgeEl && p.stack_version) badgeEl.textContent = `Official Vendor Stack · ${p.stack_version}`;
      }
    }

    // 1. Set Source Type
    const srcType = (p.source_type || "git").toLowerCase();
    const srcCard = query(`[data-source-card="${srcType}"]`);
    if (srcCard) {
      queryAll('[data-source-card]').forEach((c) => c.classList.remove('selected'));
      srcCard.classList.add('selected');
    }
    const srcInput = query('[data-source-type]');
    if (srcInput) srcInput.value = srcType;

    // 2. Set Domain if specified
    if (p.domain_name) {
      const domInput = query('#domain_id') || query('[data-domain-select]');
      const domLabel = query('[data-custom-dropdown="domain_id"] [data-dropdown-label]');
      const domItem = query(`[data-dropdown-item][data-domain-name="${p.domain_name}"]`);
      if (domItem) {
        if (domInput) {
          domInput.value = domItem.dataset.value;
          domInput.dataset.domainSsl = domItem.dataset.domainSsl || "false";
          domInput.dispatchEvent(new Event('change', { bubbles: true }));
        }
        if (domLabel) domLabel.textContent = domItem.dataset.label || p.domain_name;
        domItem.classList.add('is-selected');
      }
    }

    // 3. Set Git / Image inputs
    if (srcType === "git" || isStack) {
      const repoVal = p.repository_url || "";
      if (repoVal && query("[data-repository-url]")) query("[data-repository-url]").value = repoVal;
      if (p.branch) {
        const branchInput = query("#branch");
        if (branchInput) branchInput.value = p.branch;
        const branchLabel = query('[data-custom-dropdown="branch"] [data-dropdown-label]');
        if (branchLabel) branchLabel.textContent = p.branch;
      }
    } else if (srcType === "image") {
      if (p.image_reference && query("[data-image-reference]")) query("[data-image-reference]").value = p.image_reference;
    }

    // 4. Set Port & Build Mode & Start Command
    if (p.internal_port && query("#internal_port")) {
      query("#internal_port").value = p.internal_port;
    }
    if (p.build_mode && query("#build_mode")) {
      query("#build_mode").value = p.build_mode;
    }
    if (p.custom_start_command && query("#custom_start_command")) {
      query("#custom_start_command").value = p.custom_start_command;
    }

    // 5. Set Environment Variables (Optimal values)
    const envObj = p.environment_values || p.nonsecret_settings;
    if (envObj && typeof envObj === "object") {
      const envLines = Object.entries(envObj)
        .map(([k, v]) => `${k}=${v}`)
        .join("\n");
      parseAndApplyBulkEnv(form, envLines);
    }
    if (Array.isArray(p.secret_requirements)) {
      const secretInput = query("[data-secret-requirements]");
      if (secretInput) secretInput.value = JSON.stringify(p.secret_requirements);
    }

    // 6. Set Database Attachments
    if (Array.isArray(p.database_attachments)) {
      p.database_attachments.forEach((att) => {
        const rawKind = String(att.kind || "").toLowerCase();
        let targetKind = rawKind;
        if (rawKind.includes("postgre") || rawKind.includes("psql") || rawKind === "postgres") targetKind = "postgresql";
        else if (rawKind.includes("maria") || rawKind.includes("mysql")) targetKind = "mariadb";
        else if (rawKind.includes("redis") || rawKind.includes("valkey") || rawKind.includes("keydb")) targetKind = "redis";
        else if (rawKind.includes("mongo")) targetKind = "mongodb";

        const row = query(`[data-database-row][data-kind="${targetKind}"]`) || query(`[data-kind="${targetKind}"]`) || query(`[data-kind="${rawKind}"]`);
        if (row) {
          const chk = row.querySelector("[data-database-enabled]");
          if (chk) {
            chk.checked = true;
            row.classList.add("selected", "settings-choice--active");
            attachmentState(row);
          }
          const prov = _dbField(row, "[data-database-provider]");
          if (prov && att.provider) {
            prov.value = att.provider;
            attachmentState(row);
          }
          const keyInput = row.querySelector("[data-database-key]");
          if (keyInput && att.environment_key) keyInput.value = att.environment_key;
        }
      });
    }

    // 7. Set Storage Mounts
    if (Array.isArray(p.storage_mounts)) {
      p.storage_mounts.forEach((m) => {
        if (m.mount_path) {
          const cleanLabel = String(m.label || "data")
            .toLowerCase()
            .replace(/[^a-z0-9_-]+/g, "-")
            .replace(/^[-_]+|[-_]+$/g, "")
            .slice(0, 32) || "data";
          addStorageMountRow(form, cleanLabel, m.mount_path);
        }
      });
    }

    // 8. Advance wizard to Step 3 (Configuration) for instant visual feedback
    state.unlocked = Math.max(state.unlocked, 3);
    renderStep(3);

    // 9. Mark plan applied on backend (deduplicated)
    const pid = planData.plan_id || (p && p.plan_id);
    if (pid && !appliedPlanIds.has(pid)) {
      appliedPlanIds.add(pid);
      fetchJson(`/plugins/ai_helper/api/action-plans/${encodeURIComponent(pid)}/mark-applied`, {
        method: "POST",
        headers: csrfHeaders(),
      }).catch(() => {});
    }
  };

  // Expose step controller for in-chat AI actions
  window.advanceAiWizard = function (targetStep) {
    if (typeof targetStep === "number") {
      state.unlocked = Math.max(state.unlocked, targetStep);
      renderStep(targetStep);
      return;
    }
    if (state.step === 1) {
      inspectSource();
    } else if (state.step === 2) {
      state.unlocked = Math.max(state.unlocked, 3);
      renderStep(3);
    } else if (state.step === 3) {
      startDeployment();
    }
  };

  window.startAiDeployment = function () {
    startDeployment();
  };

  // Listen for AI assistant mode changes (split / closed)
  window.addEventListener("ai-helper:mode-change", (e) => {
    const opticEl = form.closest(".apps-engine-optic");
    if (!opticEl) return;
    if (e.detail && e.detail.active && e.detail.split) {
      opticEl.classList.add("is-ai-mode");
    } else if (e.detail && !e.detail.active) {
      opticEl.classList.remove("is-ai-mode");
    }
  });

  // Wire Set Up With AI buttons (Git and Docker Image)
  queryAll("[data-ai-setup-trigger]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!window.AiHelper) return;

      const triggerType = btn.getAttribute("data-ai-setup-trigger") || "git";
      let enteredVal = "";
      if (triggerType === "git") {
        const repoInput = query("[data-repository-url]");
        if (repoInput) enteredVal = repoInput.value.trim();
      } else if (triggerType === "image") {
        const imgInput = query("[data-image-reference]");
        if (imgInput) enteredVal = imgInput.value.trim();
      }

      // Check if domain is selected
      let domainNote = "";
      const domainInput = query("[data-domain-select]");
      const domainLabel = query('[data-custom-dropdown="domain_id"] [data-dropdown-label]');
      if (domainInput && domainInput.value && domainLabel && domainLabel.textContent && !domainLabel.textContent.includes("Select Target Domain")) {
        domainNote = " for domain " + domainLabel.textContent.trim();
      }

      let promptText = "";
      if (enteredVal) {
        promptText = "Please analyze and configure this application" + domainNote + ":\n" + enteredVal;
      } else {
        promptText = "I want to install and configure an application on my server" + domainNote + ". Here is what I want to deploy:\n\n";
      }

      window.AiHelper.open({
        split: true,
        taskType: "app_deploy",
        context: "App Engine Setup Wizard",
        initialPrompt: promptText,
      });
    });
  });

  // Handle ?plan= query parameter on page load
  const urlParams = new URLSearchParams(window.location.search);
  const planParam = urlParams.get("plan");
  if (planParam) {
    fetchJson(`/plugins/ai_helper/api/action-plans/${encodeURIComponent(planParam)}`)
      .then((data) => {
        if (data.plan) window.applyAiAppPlan(data.plan);
      })
      .catch((err) => console.warn("Could not auto-apply plan from URL:", err));
  }
}
