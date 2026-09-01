/**
 * delete-drawer.js — Shared Full-Width Bottom Delete Window Component
 * Features:
 * - Edge-to-edge full-width bottom bar (25% height)
 * - 2.5-second continuous hover activation timer with visual fill progress
 * - Zero numbers/digits displayed
 * - Touch press-and-hold fallback for mobile devices
 * - Programmatic API (window.openDeleteDrawer) & declarative triggers
 */

(function () {
  'use strict';

  let hoverTimer = null;
  let hoverStartTime = null;
  let currentConfirmCallback = null;
  const HOVER_DURATION_MS = 2500; // 2.5 seconds hover to unlock

  const getBackdrop = () => document.getElementById('delete-drawer-backdrop');
  const getDrawer = () => document.getElementById('delete-drawer');
  const getTitleEl = () => document.querySelector('[data-delete-drawer-title]');
  const getMessageEl = () => document.querySelector('[data-delete-drawer-message]');
  const getItemBoxEl = () => document.querySelector('[data-delete-drawer-item-box]');
  const getItemNameEl = () => document.querySelector('[data-delete-drawer-item]');
  const getExtraEl = () => document.querySelector('[data-delete-drawer-extra]');
  const getConfirmBtn = () => document.getElementById('delete-drawer-confirm-btn');

  function resetHoverState(btn) {
    if (hoverTimer) {
      cancelAnimationFrame(hoverTimer);
      hoverTimer = null;
    }
    hoverStartTime = null;

    if (!btn) btn = getConfirmBtn();
    if (!btn) return;

    btn.classList.remove('is-hovering', 'is-unlocked');
    btn.classList.add('is-locked');
    btn.setAttribute('aria-disabled', 'true');

    const progressEl = btn.querySelector('[data-delete-drawer-progress]');
    if (progressEl) {
      progressEl.style.width = '0%';
    }
  }

  function startHoverProgress(btn) {
    if (!btn || btn.classList.contains('is-unlocked')) return;

    if (hoverTimer) {
      cancelAnimationFrame(hoverTimer);
    }

    btn.classList.add('is-hovering');
    const progressEl = btn.querySelector('[data-delete-drawer-progress]');
    hoverStartTime = performance.now();

    function step(timestamp) {
      const elapsed = timestamp - hoverStartTime;
      const pct = Math.min(100, (elapsed / HOVER_DURATION_MS) * 100);

      if (progressEl) {
        progressEl.style.width = `${pct}%`;
      }

      if (elapsed >= HOVER_DURATION_MS) {
        // Successfully unlocked!
        btn.classList.remove('is-locked', 'is-hovering');
        btn.classList.add('is-unlocked');
        btn.removeAttribute('aria-disabled');
        if (progressEl) progressEl.style.width = '100%';
        hoverTimer = null;
        if (navigator.vibrate) {
          try { navigator.vibrate(35); } catch (_) {}
        }
      } else {
        hoverTimer = requestAnimationFrame(step);
      }
    }

    hoverTimer = requestAnimationFrame(step);
  }

  function stopHoverProgress(btn) {
    if (!btn) btn = getConfirmBtn();
    if (!btn) return;

    if (!btn.classList.contains('is-unlocked')) {
      resetHoverState(btn);
    }
  }

  function closeDeleteDrawer() {
    const backdrop = getBackdrop();
    if (!backdrop) return;
    backdrop.classList.add('hidden');
    resetHoverState();
    currentConfirmCallback = null;
  }

  function openDeleteDrawer(options = {}) {
    const backdrop = getBackdrop();
    const drawer = getDrawer();
    const titleEl = getTitleEl();
    const msgEl = getMessageEl();
    const itemBoxEl = getItemBoxEl();
    const itemNameEl = getItemNameEl();
    const extraEl = getExtraEl();
    let confirmBtn = getConfirmBtn();

    if (!backdrop || !confirmBtn) {
      // Fallback if component is missing from DOM
      if (typeof options.onConfirm === 'function') {
        const confirmed = window.confirm(options.message || 'Are you sure you want to delete this item?');
        if (confirmed) options.onConfirm();
      }
      return;
    }

    // Set texts and properties
    if (titleEl && options.title) {
      titleEl.textContent = options.title;
    }

    if (msgEl) {
      msgEl.textContent = options.message || msgEl.getAttribute('data-default-msg') || 'This action is permanent and cannot be undone.';
    }

    if (itemNameEl && itemBoxEl) {
      if (options.itemName) {
        itemNameEl.textContent = options.itemName;
        itemBoxEl.hidden = false;
      } else {
        itemBoxEl.hidden = true;
        itemNameEl.textContent = '';
      }
    }

    if (extraEl) {
      if (options.extraHtml) {
        extraEl.innerHTML = options.extraHtml;
        extraEl.hidden = false;
      } else {
        extraEl.innerHTML = '';
        extraEl.hidden = true;
      }
    }

    const baseLabel = options.okLabel || confirmBtn.getAttribute('data-base-label') || 'Delete';
    const textEl = confirmBtn.querySelector('[data-delete-drawer-btn-text]');
    if (textEl) {
      textEl.textContent = baseLabel;
    }

    // Reset button listeners via clone
    const freshBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(freshBtn, confirmBtn);
    confirmBtn = freshBtn;

    // Reset button hover states
    resetHoverState(confirmBtn);

    // Bind Pointer & Mouse & Touch events for reliable hover progress unlocking
    confirmBtn.addEventListener('mouseenter', () => startHoverProgress(confirmBtn));
    confirmBtn.addEventListener('mouseleave', () => stopHoverProgress(confirmBtn));
    confirmBtn.addEventListener('pointerenter', () => startHoverProgress(confirmBtn));
    confirmBtn.addEventListener('pointerleave', () => stopHoverProgress(confirmBtn));
    confirmBtn.addEventListener('touchstart', () => startHoverProgress(confirmBtn), { passive: true });
    confirmBtn.addEventListener('touchend', () => stopHoverProgress(confirmBtn), { passive: true });
    confirmBtn.addEventListener('touchcancel', () => stopHoverProgress(confirmBtn), { passive: true });

    // Show drawer
    backdrop.classList.remove('hidden');

    // Trigger lucide icons if available
    if (typeof lucide !== 'undefined' && lucide.createIcons) {
      lucide.createIcons({ root: drawer || backdrop });
    }

    // Save action callback
    currentConfirmCallback = async (e) => {
      e.preventDefault();
      e.stopPropagation();

      // If clicked while locked, give quick unlock or require hover
      if (!confirmBtn.classList.contains('is-unlocked')) {
        return;
      }

      closeDeleteDrawer();

      try {
        if (typeof options.onConfirm === 'function') {
          await options.onConfirm();
        } else if (options.deleteUrl) {
          const payload = {};
          if (extraEl && !extraEl.hidden) {
            extraEl.querySelectorAll('input, select, textarea').forEach(inp => {
              if (inp.name) {
                if ((inp.type === 'radio' || inp.type === 'checkbox') && !inp.checked) return;
                payload[inp.name] = inp.value;
              }
            });
          }

          if (options.deleteEvent) {
            document.dispatchEvent(new CustomEvent(options.deleteEvent, {
              detail: { payload, triggerItem: options.itemName, deleteUrl: options.deleteUrl }
            }));
          } else if (typeof window.submitPost === 'function') {
            window.submitPost(options.deleteUrl, payload);
          } else {
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = options.deleteUrl;
            Object.entries(payload).forEach(([k, v]) => {
              const hidden = document.createElement('input');
              hidden.type = 'hidden';
              hidden.name = k;
              hidden.value = v;
              form.appendChild(hidden);
            });
            document.body.appendChild(form);
            form.submit();
          }
        }
      } catch (err) {
        console.error('Delete action failed:', err);
        if (typeof window.toast === 'function') {
          window.toast(err.message || 'Delete operation failed', 'danger');
        }
      }
    };

    confirmBtn.addEventListener('click', currentConfirmCallback);
  }

  // Global listeners (Close buttons, backdrop click, Escape key)
  document.addEventListener('DOMContentLoaded', () => {
    // Backdrop click
    const backdrop = getBackdrop();
    if (backdrop) {
      backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) {
          closeDeleteDrawer();
        }
      });
    }

    // Close buttons & Declarative triggers
    document.addEventListener('click', (e) => {
      const closeBtn = e.target.closest('[data-delete-drawer-close]');
      if (closeBtn) {
        e.preventDefault();
        closeDeleteDrawer();
        return;
      }

      // Declarative trigger
      const trigger = e.target.closest('[data-delete-drawer-trigger]');
      if (trigger) {
        e.preventDefault();
        const deleteUrl = trigger.getAttribute('data-delete-url') || trigger.getAttribute('href');
        const title = trigger.getAttribute('data-delete-title');
        const message = trigger.getAttribute('data-delete-message');
        const itemName = trigger.getAttribute('data-delete-item');
        const okLabel = trigger.getAttribute('data-delete-label');
        const extraId = trigger.getAttribute('data-delete-extra-id');
        const deleteEvent = trigger.getAttribute('data-delete-event');

        let extraHtml = '';
        if (extraId) {
          const tpl = document.getElementById(extraId);
          if (tpl) extraHtml = tpl.innerHTML;
        }

        openDeleteDrawer({
          title,
          message,
          itemName,
          deleteUrl,
          deleteEvent,
          okLabel,
          extraHtml
        });
      }
    });

    // Escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const backdrop = getBackdrop();
        if (backdrop && !backdrop.classList.contains('hidden')) {
          closeDeleteDrawer();
        }
      }
    });
  });

  // Expose public API
  window.openDeleteDrawer = openDeleteDrawer;
  window.closeDeleteDrawer = closeDeleteDrawer;
})();
