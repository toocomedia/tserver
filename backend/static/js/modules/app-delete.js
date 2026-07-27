const modal = document.querySelector('[data-app-delete-modal]');

if (modal) {
  const close = () => modal.classList.add('hidden');
  document.querySelectorAll('[data-app-delete-open]').forEach((button) => {
    button.addEventListener('click', () => modal.classList.remove('hidden'));
  });
  modal.querySelectorAll('[data-app-delete-close]').forEach((button) => {
    button.addEventListener('click', close);
  });
  modal.addEventListener('click', (event) => {
    if (event.target === modal) close();
  });
}
