/**
 * task-shield.js — Global Concurrency Shield & Zero-Reload Form Interceptor
 * Features:
 * - Automatically locks conflicting action buttons during heavy background tasks
 * - Intercepts plugin, dependency, and system forms to run asynchronously via TaskManager
 * - Dispatches local component refresh on task:completed without full page reload
 */
(function () {
  'use strict';

  function handleLockUpdate(locks) {
    const isBusy = locks.apt_locked || locks.exclusive_locked || locks.build_locked;
    const summary = (locks.running_summary || []).map((s) => s.label).join(', ');
    const lockReason = summary ? `Server task running: ${summary}` : 'A background task is currently running';

    document.querySelectorAll('[data-task-lockable], form[action*="/plugin-manager/api/"] button[type="submit"], form[action*="/api/dependencies/"] button[type="submit"]').forEach((btn) => {
      const form = btn.closest('form');
      if (form && (form.action.includes('/check') || form.action.includes('/info'))) return;

      if (isBusy) {
        if (!btn.hasAttribute('data-shield-locked')) {
          btn.setAttribute('data-shield-locked', 'true');
          btn.dataset.shieldOriginalDisabled = btn.disabled ? 'true' : 'false';
          btn.dataset.shieldOriginalTitle = btn.title || '';
        }
        btn.disabled = true;
        btn.title = lockReason;
        btn.classList.add('is-task-locked');
      } else if (btn.hasAttribute('data-shield-locked')) {
        btn.disabled = btn.dataset.shieldOriginalDisabled === 'true';
        btn.title = btn.dataset.shieldOriginalTitle || '';
        btn.removeAttribute('data-shield-locked');
        btn.classList.remove('is-task-locked');
      }
    });
  }

  function interceptActionForms() {
    document.addEventListener('submit', async (e) => {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;

      const asyncRedirect = form.getAttribute('data-async-redirect');
      const action = form.action || '';

      if (asyncRedirect) {
        e.preventDefault();
        e.stopPropagation();
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.classList.add('is-loading');
        }
        try {
          const payload = Object.fromEntries(new FormData(form).entries());
          form.querySelectorAll('input[type=checkbox]').forEach((cb) => {
            payload[cb.name] = cb.checked;
          });
          await window.submitAsyncForm(action, payload, asyncRedirect);
        } catch (err) {
          if (window.toast) window.toast(err.message || 'Operation failed', 'danger');
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove('is-loading');
          }
        }
        return;
      }

      const isPluginAction = action.includes('/plugin-manager/api/');
      const isDepAction = action.includes('/api/dependencies/') && (action.includes('/install') || action.includes('/update') || action.includes('/toggle'));

      if (!isPluginAction && !isDepAction) return;

      // Skip search / check queries
      if (action.includes('/check') || action.includes('/catalog-view') || action.includes('/runtime-view')) return;

      // Data purge confirmation handling
      if (action.includes('/purge-data')) {
        const confInput = form.querySelector('input[name="confirmation"]');
        if (!confInput || !confInput.value) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
      }

      e.preventDefault();
      e.stopPropagation();

      const csrf = form.querySelector('input[name="csrf_token"]')?.value || 
                   document.querySelector('meta[name="csrf-token"]')?.content || '';

      const submitBtn = form.querySelector('button[type="submit"]');
      if (submitBtn) {
        submitBtn.disabled = true;
      }

      try {
        const formData = new FormData(form);
        const res = await fetch(action, {
          method: 'POST',
          headers: {
            'Accept': 'application/json',
            'X-CSRF-Token': csrf,
          },
          body: formData,
        });

        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
          throw new Error(data.detail || data.message || 'Task failed to start');
        }

        if (window.toast) {
          window.toast(data.message || 'Task started in background', 'success');
        }

        // Open task drawer and poll immediately
        if (typeof window.openTaskDrawer === 'function') {
          window.openTaskDrawer('active');
        }
        if (typeof window.refreshTasks === 'function') {
          window.refreshTasks();
        }
      } catch (err) {
        console.error('Async action error:', err);
        if (window.toast) {
          window.toast(err.message || 'Operation failed', 'danger');
        } else {
          alert(err.message || 'Operation failed');
        }
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
        }
      }
    }, true);
  }

  function bindTaskCompletedRefresh() {
    document.addEventListener('task:completed', () => {
      // 1. Dependency Catalog refresh
      const depCatalog = document.querySelector('[data-dependency-catalog]');
      if (depCatalog && typeof window.loadCatalog === 'function') {
        window.loadCatalog();
      }

      // 2. PHP Runtime view refresh
      const phpRuntime = document.querySelector('[data-php-runtime]');
      if (phpRuntime && typeof window.loadRuntime === 'function') {
        window.loadRuntime();
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    interceptActionForms();
    bindTaskCompletedRefresh();
    document.addEventListener('task:locks-updated', (e) => handleLockUpdate(e.detail || {}));
  });
})();
