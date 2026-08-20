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

const editModal = document.querySelector('[data-edit-settings-modal]');
const editOpenBtn = document.querySelector('[data-edit-settings-open]');
const editCloseBtns = document.querySelectorAll('[data-edit-settings-close]');

if (editModal && editOpenBtn) {
  editOpenBtn.addEventListener('click', () => {
    editModal.classList.remove('hidden');
  });
  editCloseBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      editModal.classList.add('hidden');
    });
  });
  editModal.addEventListener('click', (e) => {
    if (e.target === editModal) editModal.classList.add('hidden');
  });

  const addMountBtn = editModal.querySelector('[data-edit-add-mount]');
  const mountsList = editModal.querySelector('[data-edit-mounts-list]');
  const storageMountsInput = editModal.querySelector('[data-edit-storage-mounts]');
  const editForm = editModal.querySelector('form');

  if (addMountBtn && mountsList) {
    addMountBtn.addEventListener('click', () => {
      const row = document.createElement('div');
      row.style.cssText = 'display: flex; gap: 8px; align-items: center;';
      row.dataset.editMountRow = '';

      const labelInp = document.createElement('input');
      labelInp.className = 'form-input form-input--code';
      labelInp.style.flex = '1';
      labelInp.placeholder = 'Label';
      labelInp.dataset.editMountLabel = '';

      const pathInp = document.createElement('input');
      pathInp.className = 'form-input form-input--code';
      pathInp.style.flex = '1.5';
      pathInp.placeholder = 'Mount Path (e.g. /app/uploads)';
      pathInp.dataset.editMountPath = '';

      const rmBtn = document.createElement('button');
      rmBtn.className = 'btn btn--secondary btn--sm';
      rmBtn.type = 'button';
      rmBtn.textContent = 'Remove';
      rmBtn.addEventListener('click', () => row.remove());

      row.append(labelInp, pathInp, rmBtn);
      mountsList.appendChild(row);
    });

    mountsList.querySelectorAll('[data-edit-mount-remove]').forEach(btn => {
      btn.addEventListener('click', () => {
        btn.closest('[data-edit-mount-row]')?.remove();
      });
    });
  }

  if (editForm && storageMountsInput && mountsList) {
    editForm.addEventListener('submit', (e) => {
      const mounts = [];
      mountsList.querySelectorAll('[data-edit-mount-row]').forEach(row => {
        const label = row.querySelector('[data-edit-mount-label]')?.value.trim().toLowerCase() || '';
        const path = row.querySelector('[data-edit-mount-path]')?.value.trim() || '';
        if (label || path) {
          mounts.push({ label, mount_path: path });
        }
      });
      storageMountsInput.value = JSON.stringify(mounts);
    });
  }
}
