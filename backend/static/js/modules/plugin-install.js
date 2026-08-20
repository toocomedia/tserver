(function bindUnverifiedPluginInstall() {
  function bind() {
    document.querySelectorAll('[data-unverified-install]').forEach(function (form) {
      if (form.dataset.unverifiedBound === 'true') return;
      form.dataset.unverifiedBound = 'true';
      form.addEventListener('submit', function (event) {
        var expected = form.dataset.approvalToken || '';
        var input = form.querySelector('input[name="unverified_confirmation"]');
        if (input && input.value === expected) return;
        event.preventDefault();
        if (!window.confirm(form.dataset.confirm || '')) return;
        input.value = expected;
        form.requestSubmit();
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
