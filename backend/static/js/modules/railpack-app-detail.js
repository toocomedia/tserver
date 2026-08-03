const deployment = document.querySelector('[data-railpack-deployment]');
const deleteModal = document.querySelector('[data-railpack-delete-modal]');

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

if (deleteModal) {
  document.querySelector('[data-railpack-delete-open]')?.addEventListener('click', () => deleteModal.classList.remove('hidden'));
  deleteModal.querySelectorAll('[data-railpack-delete-close]').forEach((button) => button.addEventListener('click', () => deleteModal.classList.add('hidden')));
}
