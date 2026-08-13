import {
  csrfHeaders, environmentValues, fetchJson, renderBranches, renderDeploymentSteps, renderDetection,
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
  const supabasePicker = root.querySelector('[data-supabase-picker]');
  const supabaseProject = root.querySelector('[data-supabase-project]');
  const appPort = root.querySelector('[data-app-port]');
  const portStatus = root.querySelector('[data-port-status]');
  const state = { step: 1, unlocked: 1, detected: null, appId: null, deploymentId: null };
  let branchTimer;

  const panel = (step) => root.querySelector(`[data-wizard-panel="${step}"]`);
  const nav = (step) => root.querySelector(`[data-wizard-nav="${step}"]`);

  function renderStep(step) {
    state.step = step;
    for (let index = 1; index <= 6; index += 1) {
      setHidden(panel(index), index !== step);
      nav(index).classList.toggle('is-active', index === step);
      nav(index).classList.toggle('active', index === step);
      nav(index).classList.toggle('disabled', index > state.unlocked);
    }
    back.hidden = step === 1 || step >= 5;
    cancel.hidden = step >= 5;
    next.hidden = step >= 5;
    next.disabled = step === 2 && !state.detected;
    if (step === 1) next.textContent = window._('js.continue_to_detection');
    if (step === 2) next.textContent = window._('js.continue_to_configuration');
    if (step === 3) next.textContent = window._('js.continue_to_environment');
    if (step === 4) next.textContent = window._('deploy_app');
    setText(root.querySelector('[data-wizard-hint]'), window._('js.step_n_of_6').replace('{step}', step));
  }

  function setNextLoading(loading, label) {
    next.disabled = loading;
    next.classList.toggle('is-loading', loading);
    if (!loading) next.textContent = label;
  }

  function resetBranches() {
    branch.disabled = true;
    branch.replaceChildren(new Option(window._('js.loading_branches')));
    setText(branchMessage, window._('js.branches_will_load'));
  }

  async function loadBranches() {
    const url = repo.value.trim();
    if (!url) throw new Error(window._('js.enter_git_url_first'));
    resetBranches();
    const values = new FormData();
    values.append('repository_url', url);
    const data = await fetchJson('/apps/branches', {
      method: 'POST', headers: csrfHeaders(), body: values,
    });
    repo.value = data.repository_url || url;
    renderBranches(branch, data.branches, data.default_branch);
    const msgKey = data.branches.length === 1 ? 'js.branch_available' : 'js.branches_available';
    setText(branchMessage, window._(msgKey).replace('{count}', data.branches.length));
  }

  function showDetectionError(error) {
    state.unlocked = 2;
    renderStep(2);
    setHidden(root.querySelector('[data-detection-loading]'), true);
    setHidden(root.querySelector('[data-detection-results]'), true);
    setText(root.querySelector('[data-detection-error-text]'), error.message || window._('js.detection_failed'));
    setHidden(root.querySelector('[data-detection-error]'), false);
    next.disabled = true;
  }

  function configureDetectedProject(detected) {
    root.querySelector('#build_command').value = detected.build_command || '';
    root.querySelector('#start_command').value = detected.start_command || '';
    const environmentKeys = (detected.environment_keys || []).filter((item) => item.name !== 'DATABASE_URL');
    renderEnvironmentFields(root.querySelector('[data-environment-list]'), environmentKeys);
    setHidden(root.querySelector('[data-environment-empty]'), Boolean(environmentKeys.length));
    const evidence = detected.database_evidence || [];
    setText(root.querySelector('[data-database-hint]'), evidence.length ? evidence.join(' · ') : window._('js.no_database_detected'));
    const supabaseOption = root.querySelector('[data-supabase-option]');
    if (supabaseOption) supabaseOption.hidden = !detected.postgres_suspected;
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
    const supabase = databaseMode.value === 'supabase';
    setHidden(externalDatabase, !external);
    databaseUrl.required = external;
    if (supabasePicker) setHidden(supabasePicker, !supabase);
    if (supabaseProject) supabaseProject.required = supabase;
  }

  async function checkPort() {
    if (!appPort.reportValidity()) return false;
    setText(portStatus, window._('js.checking_port').replace('{port}', appPort.value));
    try {
      const result = await fetchJson(`/apps/port-availability?port=${encodeURIComponent(appPort.value)}`);
      setText(portStatus, window._('js.port_available').replace('{port}', result.port));
      return true;
    } catch (error) {
      setText(portStatus, error.message || window._('js.port_check_failed'));
      return false;
    }
  }

  function configurationError(message) {
    const alert = panel(state.step).querySelector('[data-configuration-error]');
    setText(alert, message);
    setHidden(alert, false);
  }

  function validConfiguration() {
    root.querySelectorAll('[data-configuration-error]').forEach((alert) => setHidden(alert, true));
    const fields = [root.querySelector('#build_command'), root.querySelector('#start_command'), appPort];
    if (databaseMode.value === 'external') fields.push(databaseUrl);
    if (databaseMode.value === 'supabase' && supabaseProject) fields.push(supabaseProject);
    if (!fields.every((field) => field.reportValidity())) return false;
    const databaseRequired = state.detected?.environment_keys?.some((item) => item.name === 'DATABASE_URL' && item.required);
    if (databaseRequired && databaseMode.value === 'none') {
      configurationError(window._('js.database_url_required'));
      return false;
    }
    return true;
  }

  async function validEnvironment() {
    if (!validConfiguration()) {
      renderStep(3);
      return false;
    }
    if (!await checkPort()) {
      renderStep(3);
      return false;
    }
    const inputs = [...root.querySelectorAll('[data-environment-key]')];
    if (!inputs.every((input) => input.reportValidity())) return false;
    root.querySelector('[data-environment-values]').value = JSON.stringify(environmentValues(root));
    return true;
  }

  async function startDeployment() {
    if (!await validEnvironment()) return;
    setNextLoading(true, window._('js.deploying'));
    try {
      const result = await fetchJson('/apps/create', {
        method: 'POST', headers: { ...csrfHeaders(), Accept: 'application/json' }, body: new FormData(form),
      });
      state.appId = result.app_id;
      state.deploymentId = result.deployment_id;
      state.unlocked = 5;
      renderStep(5);
      setHidden(actions, true);
      pollDeployment();
    } catch (error) {
      configurationError(error.message || window._('js.deployment_could_not_start'));
    } finally {
      setNextLoading(false, window._('deploy_app'));
    }
  }

  async function pollDeployment() {
    try {
      const data = await fetchJson(`/apps/${state.appId}/deployments/${state.deploymentId}`, { headers: csrfHeaders() });
      root.querySelectorAll('[data-deployment-stage]').forEach((item) => setText(item, `${data.status} · ${data.stage}`));
      setText(root.querySelector('[data-deployment-summary]'), data.status === 'success' ? window._('js.deployment_completed') : window._('js.deployment_running'));
      setText(root.querySelector('[data-deployment-output]'), `${data.output || ''}${data.error || ''}`);
      renderDeploymentSteps(root.querySelector('[data-deployment-steps]'), data.stage);
      if (['queued', 'running'].includes(data.status)) return window.setTimeout(pollDeployment, 1200);
      if (data.status === 'success') {
        state.unlocked = 6;
        const dashboard = root.querySelector('[data-deployment-dashboard]');
        dashboard.href = `/apps/${state.appId}`;
        setHidden(dashboard, false);
        renderStep(6);
        return;
      }
      state.unlocked = 6;
      setText(root.querySelector('[data-deployment-summary]'), window._('js.deployment_failed_review'));
      setText(root.querySelector('[data-deployment-error-text]'), data.error || window._('js.deployment_failed'));
      root.querySelector('[data-deployment-details]').href = `/apps/${state.appId}`;
      setHidden(root.querySelector('[data-deployment-error]'), false);
      renderStep(6);
    } catch (error) {
      setText(root.querySelector('[data-deployment-error-text]'), error.message || window._('js.could_not_read_status'));
      setHidden(root.querySelector('[data-deployment-error]'), false);
    }
  }
  repo.addEventListener('input', () => {
    window.clearTimeout(branchTimer);
    resetBranches();
    branchTimer = window.setTimeout(() => loadBranches().catch((error) => setText(branchMessage, error.message)), 700);
  });
  databaseMode.addEventListener('change', syncDatabaseMode);
  appPort.addEventListener('input', () => setText(portStatus, window._('js.port_changed')));
  root.querySelector('[data-check-port]').addEventListener('click', checkPort);
  root.querySelector('[data-detection-retry]').addEventListener('click', detect);
  next.addEventListener('click', () => [detect, () => renderStep(3), () => { if (validConfiguration()) renderStep(4); }, startDeployment][state.step - 1]?.());
  back.addEventListener('click', () => renderStep(Math.max(1, state.step - 1)));
  root.querySelectorAll('[data-wizard-nav]').forEach((button) => button.addEventListener('click', () => {
    if (Number(button.dataset.wizardNav) <= state.unlocked) renderStep(Number(button.dataset.wizardNav));
  }));
  renderStep(1);
}
