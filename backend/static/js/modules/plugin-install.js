(function bindUnverifiedPluginInstall() {
  function bind() {
    document.querySelectorAll('[data-unverified-install]').forEach(function (form) {
      if (form.dataset.unverifiedBound === 'true') return;
      form.dataset.unverifiedBound = 'true';
      form.addEventListener('submit', function (event) {
        var expected = form.dataset.confirmation || '';
        var input = form.querySelector('input[name="unverified_confirmation"]');
        if (input && input.value === expected) return;
        event.preventDefault();
        var entered = window.prompt((form.dataset.prompt || '') + '\n\n' + expected);
        if (entered === null) return;
        if (entered.trim() !== expected) {
          window.alert(form.dataset.error || '');
          return;
        }
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
