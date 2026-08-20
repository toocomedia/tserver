/**
 * app-delete.js — Controls the App Deletion / Danger Zone confirmation modal.
 */

export function initAppDelete() {
  const openBtn = document.querySelector('[data-app-delete-open]');
  const modal = document.querySelector('[data-app-delete-modal]');
  const closeBtns = document.querySelectorAll('[data-app-delete-close]');
  const confirmInput = modal?.querySelector('#confirmation');
  const submitBtn = modal?.querySelector('button[type="submit"]');
  const form = modal?.querySelector('form');

  if (!modal || !openBtn) return;

  function openModal() {
    modal.classList.remove('hidden');
    if (confirmInput) {
      confirmInput.value = '';
      confirmInput.focus();
    }
  }

  function closeModal() {
    modal.classList.add('hidden');
  }

  openBtn.addEventListener('click', openModal);

  closeBtns.forEach((btn) => {
    btn.addEventListener('click', closeModal);
  });

  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      closeModal();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
      closeModal();
    }
  });

  if (form) {
    form.addEventListener('submit', (e) => {
      const val = (confirmInput?.value || '').trim();
      if (val !== 'DELETE ALL') {
        e.preventDefault();
        alert('Please type DELETE ALL in capital letters to confirm deletion.');
        if (confirmInput) confirmInput.focus();
        return;
      }
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Deleting...';
      }
    });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAppDelete);
} else {
  initAppDelete();
}
