(() => {
  const root = document.querySelector('[data-deployment-url]');
  if (!root) return;
  const state = root.querySelector('[data-deployment-state]');
  const output = root.querySelector('[data-deployment-output]');
  const details = root.querySelector('[data-deployment-details]');
  const rollbackRow = root.querySelector('[data-rollback-row]');
  const rollbackState = root.querySelector('[data-rollback-state]');

  const poll = async () => {
    try {
      const response = await fetch(root.dataset.deploymentUrl);
      if (!response.ok) return;
      const data = await response.json();
      state.textContent = `${data.status.toUpperCase()} · ${data.stage}`;
      output.textContent = (data.output || '') + (data.error || '');
      if (data.rollback_status) {
        rollbackRow.hidden = false;
        rollbackState.textContent = data.rollback_status;
      }
      if (['queued', 'running'].includes(data.status)) {
        details.open = true;
        window.setTimeout(poll, 2000);
      } else {
        window.setTimeout(() => window.location.reload(), 800);
      }
    } catch (_) {
      window.setTimeout(poll, 3000);
    }
  };
  window.setTimeout(poll, 1200);
})();
