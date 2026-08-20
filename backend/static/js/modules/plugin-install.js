(function bindUnverifiedPluginInstall() {
  function unlock(form) {
    form.removeAttribute('data-submitting');
    form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (button) {
      button.disabled = false;
      button.removeAttribute('aria-disabled');
      if (button.tagName === 'BUTTON' && button.dataset.originalLabel) {
        button.textContent = button.dataset.originalLabel;
        delete button.dataset.originalLabel;
      }
    });
  }

  function bind() {
    document.querySelectorAll('[data-unverified-install]').forEach(function (form) {
      if (form.dataset.unverifiedBound === 'true') return;
      form.dataset.unverifiedBound = 'true';
      form.addEventListener('submit', function (event) {
        var expected = form.dataset.approvalToken || '';
        var input = form.querySelector('input[name="unverified_confirmation"]');
        if (input && input.value === expected) return;
        event.preventDefault();
        if (!input || !expected || !window.confirm(form.dataset.confirm || '')) {
          unlock(form);
          return;
        }
        input.value = expected;
        HTMLFormElement.prototype.submit.call(form);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
