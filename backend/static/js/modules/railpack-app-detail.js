const deployment = document.querySelector('[data-railpack-deployment]');

async function poll() {
  try {
    const response = await fetch(deployment.dataset.deploymentUrl);
    const item = await response.json();
    if (!response.ok) throw new Error(item.detail || 'Could not refresh deployment.');
    deployment.querySelector('[data-deployment-state]').textContent = `${item.status} · ${item.stage}`;
    deployment.querySelector('[data-deployment-output]').textContent = item.output + (item.error ? `\n[error] ${item.error}` : '');
    if (['queued', 'running'].includes(item.status)) setTimeout(poll, 1500);
    else window.location.reload();
  } catch (_) { setTimeout(poll, 4000); }
}

if (deployment?.dataset.deploymentActive === 'true') setTimeout(poll, 800);
