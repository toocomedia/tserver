import {
  csrfHeaders, environmentValues, fetchJson, renderBranches, renderDetection,
  renderEnvironmentFields, setHidden, setText,
} from './app-create-ui.js';

const root = document.querySelector('[data-app-wizard]');
if (root) {
  const form = root;
  const repo = root.querySelector('[data-repository-url]');
  const branch = root.querySelector('[data-branch-select]');
  const branchMessage = root.querySelector('[data-branch-message]');
  const next = root.querySelector('[data-wizard-next]');
  const back = root.querySelector('[data-wizard-back]');
  const cancel = root.querySelector('[data-wizard-cancel]');
  const actions = root.querySelector('[data-wizard-actions]');
  const databaseMode = root.querySelector('[data-postgres-mode]');
  const externalDatabase = root.querySelector('[data-external-database]');
  const databaseUrl = root.querySelector('#database_url');
  const state = { step: 1, unlocked: 1, detected: null, appId: null, deploymentId: null };
  let branchTimer;

  const panel = (step) => root.querySelector(`[data-wizard-panel="${step}"]`);
  const nav = (step) => root.querySelector(`[data-wizard-nav="${step}"]`);

  function renderStep(step) {
    state.step = step;
    for (let index = 1; index <= 4; index += 1) {
      setHidden(panel(index), index !== step);
      nav(index).classList.toggle('is-active', index === step);
      nav(index).disabled = index > state.unlocked;
    }
    back.hidden = step === 1 || step === 4;
    cancel.hidden = step === 4;
    next.hidden = step === 4;
    next.disabled = step === 2 && !state.detected;
    if (step === 1) next.textContent = 'Continue to Detection';
    if (step === 2) next.textContent = 'Continue to Configuration';
    if (step === 3) next.textContent = 'Deploy App';
  }

  function setNextLoading(loading, label) {
    next.disabled = loading;
    next.classList.toggle('is-loading', loading);
    if (!loading) next.textContent = label;
  }

  function resetBranches() {
    branch.disabled = true;
    branch.replaceChildren(new Option('Loading branches…'));
    setText(branchMessage, 'Branches will load from the repository.');
  }

  async function loadBranches() {
    const url = repo.value.trim();
    if (!url) throw new Error('Enter a Git repository URL first.');
    resetBranches();
    const values = new FormData();
    values.append('repository_url', url);
    const data = await fetchJson('/apps/branches', {
      method: 'POST', headers: csrfHeaders(), body: values,
    });
    repo.value = data.repository_url || url;
    renderBranches(branch, data.branches, data.default_branch);
    setText(branchMessage, `${data.branches.length} branch${data.branches.length === 1 ? '' : 'es'} available.`);
  }

  function showDetectionError(error) {
    state.unlocked = 2;
    renderStep(2);
    setHidden(root.querySelector('[data-detection-loading]'), true);
    setHidden(root.querySelector('[data-detection-results]'), true);
    setText(root.querySelector('[data-detection-error-text]'), error.message || 'Detection failed.');
    setHidden(root.querySelector('[data-detection-error]'), false);
    next.disabled = true;
  }

  function configureDetectedProject(detected) {
    root.querySelector('#build_command').value = detected.build_command || '';
    root.querySelector('#start_command').value = detected.start_command || '';
    renderEnvironmentFields(root.querySelector('[data-environment-list]'), detected.environment_keys || []);
    setHidden(root.querySelector('[data-environment-fields]'), !(detected.environment_keys || []).some((item) => item.name !== 'DATABASE_URL'));
    const evidence = detected.database_evidence || [];
    setText(root.querySelector('[data-database-hint]'), evidence.length ? evidence.join(' · ') : 'No database was detected.');
    databaseMode.value = detected.managed_postgres_recommended ? 'create' : 'none';
    syncDatabaseMode();
  }

  async function detect() {
    state.unlocked = 2;
    renderStep(2);
    setHidden(root.querySelector('[data-detection-error]'), true);
    setHidden(root.querySelector('[data-detection-results]'), true);
    setHidden(root.querySelector('[data-detection-loading]'), false);
    next.disabled = true;
    try {
      if (branch.disabled) await loadBranches();
      const detected = await fetchJson('/apps/inspect', {
        method: 'POST', headers: csrfHeaders(), body: new FormData(form),
      });
      state.detected = detected;
      repo.value = detected.repository_url || repo.value;
      branch.value = detected.branch || branch.value;
      renderDetection(root, detected);
      configureDetectedProject(detected);
      setHidden(root.querySelector('[data-detection-loading]'), true);
      setHidden(root.querySelector('[data-detection-results]'), false);
      state.unlocked = 3;
      next.disabled = false;
    } catch (error) {
      showDetectionError(error);
    }
  }

  function syncDatabaseMode() {
    const external = databaseMode.value === 'external';
    setHidden(externalDatabase, !external);
    databaseUrl.required = external;
  }

  function configurationError(message) {
    const alert = root.querySelector('[data-configuration-error]');
    setText(alert, message);
    setHidden(alert, false);
  }

  function validConfiguration() {
    setHidden(root.querySelector('[data-configuration-error]'), true);
    if (!form.reportValidity()) return false;
    const databaseRequired = state.detected?.environment_keys?.some((item) => item.name === 'DATABASE_URL' && item.required);
    if (databaseRequired && databaseMode.value === 'none') {
      configurationError('DATABASE_URL is required. Select managed PostgreSQL or provide an external URL.');
      return false;
    }
    root.querySelector('[data-environment-values]').value = JSON.stringify(environmentValues(root));
    return true;
  }

  async function startDeployment() {
    if (!validConfiguration()) return;
    setNextLoading(true, 'Deploying');
    try {
      const result = await fetchJson('/apps/create', {
        method: 'POST', headers: { ...csrfHeaders(), Accept: 'application/json' }, body: new FormData(form),
      });
      state.appId = result.app_id;
      state.deploymentId = result.deployment_id;
      state.unlocked = 4;
      renderStep(4);
      setHidden(actions, true);
      pollDeployment();
    } catch (error) {
      configurationError(error.message || 'Deployment could not be started.');
    } finally {
      setNextLoading(false, 'Deploy App');
    }
  }

  async function pollDeployment() {
    try {
      const data = await fetchJson(`/apps/${state.appId}/deployments/${state.deploymentId}`, { headers: csrfHeaders() });
      setText(root.querySelector('[data-deployment-stage]'), `${data.status} · ${data.stage}`);
      setText(root.querySelector('[data-deployment-summary]'), data.status === 'success' ? 'Deployment completed successfully.' : 'Deployment is running on the server.');
      setText(root.querySelector('[data-deployment-output]'), `${data.output || ''}${data.error || ''}`);
      if (['queued', 'running'].includes(data.status)) return window.setTimeout(pollDeployment, 1200);
      if (data.status === 'success') {
        const dashboard = root.querySelector('[data-deployment-dashboard]');
        dashboard.href = `/apps/${state.appId}`;
        setHidden(dashboard, false);
        return;
      }
      setText(root.querySelector('[data-deployment-summary]'), 'Deployment failed. Review the output, then retry from app details.');
      setText(root.querySelector('[data-deployment-error-text]'), data.error || 'Deployment failed.');
      root.querySelector('[data-deployment-details]').href = `/apps/${state.appId}`;
      setHidden(root.querySelector('[data-deployment-error]'), false);
    } catch (error) {
      setText(root.querySelector('[data-deployment-error-text]'), error.message || 'Could not read deployment status.');
      setHidden(root.querySelector('[data-deployment-error]'), false);
    }
  }

  repo.addEventListener('input', () => {
    window.clearTimeout(branchTimer);
    resetBranches();
    branchTimer = window.setTimeout(() => loadBranches().catch((error) => setText(branchMessage, error.message)), 700);
  });
  databaseMode.addEventListener('change', syncDatabaseMode);
  root.querySelector('[data-detection-retry]').addEventListener('click', detect);
  next.addEventListener('click', () => {
    if (state.step === 1) detect();
    else if (state.step === 2) renderStep(3);
    else if (state.step === 3) startDeployment();
  });
  back.addEventListener('click', () => renderStep(Math.max(1, state.step - 1)));
  root.querySelectorAll('[data-wizard-nav]').forEach((button) => button.addEventListener('click', () => {
    const step = Number(button.dataset.wizardNav);
    if (step <= state.unlocked) renderStep(step);
  }));
  renderStep(1);
}
