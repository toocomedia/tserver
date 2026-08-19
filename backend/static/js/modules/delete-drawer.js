/**
 * delete-drawer.js — Shared Side Split Delete Drawer
 * Features:
 * - Slide-in split drawer with dark opacity backdrop
 * - 5-second countdown timer before confirm button activates
 * - Programmatic API (window.openDeleteDrawer) & declarative triggers
 * - Translatable strings & RTL support
 */

(function () {
  'use strict';

  let countdownInterval = null;
  let currentConfirmCallback = null;

  const getBackdrop = () => document.getElementById('delete-drawer-backdrop');
  const getDrawer = () => document.getElementById('delete-drawer');
  const getTitleEl = () => document.querySelector('[data-delete-drawer-title]');
  const getMessageEl = () => document.querySelector('[data-delete-drawer-message]');
  const getItemBoxEl = () => document.querySelector('[data-delete-drawer-item-box]');
  const getItemNameEl = () => document.querySelector('[data-delete-drawer-item]');
  const getExtraEl = () => document.querySelector('[data-delete-drawer-extra]');
  const getConfirmBtn = () => document.getElementById('delete-drawer-confirm-btn');

  function clearCountdown() {
    if (countdownInterval) {
      clearInterval(countdownInterval);
      countdownInterval = null;
    }
  }

  function resetConfirmButton(btn) {
    if (!btn) return;
    clearCountdown();
    btn.disabled = true;
    const baseLabel = btn.getAttribute('data-base-label') || 'Delete';
    const textEl = btn.querySelector('[data-delete-drawer-btn-text]');
    if (textEl) {
      textEl.textContent = `${baseLabel} (5s)`;
    }
  }

  function startCountdown(btn, duration = 5, baseLabel = 'Delete', countdownTpl = 'Delete ({sec}s)') {
    if (!btn) return;
    clearCountdown();

    let remaining = duration;
    btn.disabled = true;

    const textEl = btn.querySelector('[data-delete-drawer-btn-text]');
    const updateLabel = (sec) => {
      if (textEl) {
        if (countdownTpl && countdownTpl.includes('{sec}')) {
          textEl.textContent = countdownTpl.replace('{sec}', sec);
        } else {
          textEl.textContent = `${baseLabel} (${sec}s)`;
        }
      }
    };

    updateLabel(remaining);

    countdownInterval = setInterval(() => {
      remaining -= 1;
      if (remaining > 0) {
        updateLabel(remaining);
      } else {
        clearCountdown();
        btn.disabled = false;
        if (textEl) {
          textEl.textContent = baseLabel;
        }
      }
    }, 1000);
  }

  function closeDeleteDrawer() {
    const backdrop = getBackdrop();
    if (!backdrop) return;
    backdrop.classList.add('hidden');
    clearCountdown();
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

    const duration = typeof options.countdown === 'number' ? options.countdown : 5;
    const baseLabel = options.okLabel || confirmBtn.getAttribute('data-base-label') || 'Delete';
    const countdownTpl = confirmBtn.getAttribute('data-countdown-tpl') || 'Delete ({sec}s)';

    // Reset button listeners via clone
    const freshBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(freshBtn, confirmBtn);
    confirmBtn = freshBtn;

    // Show drawer
    backdrop.classList.remove('hidden');

    // Trigger lucide icons if available
    if (typeof lucide !== 'undefined' && lucide.createIcons) {
      lucide.createIcons({ root: drawer || backdrop });
    }

    // Start 5s countdown
    startCountdown(confirmBtn, duration, baseLabel, countdownTpl);

    // Save action callback
    currentConfirmCallback = async (e) => {
      e.preventDefault();
      e.stopPropagation();

      if (confirmBtn.disabled) return;

      closeDeleteDrawer();

      try {
        if (typeof options.onConfirm === 'function') {
          await options.onConfirm();
        } else if (options.deleteUrl) {
          if (typeof window.submitPost === 'function') {
            window.submitPost(options.deleteUrl);
          } else {
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = options.deleteUrl;
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

    confirmBtn.addEventListener('click', currentConfirmCallback, { once: true });
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

    // Close buttons
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

        openDeleteDrawer({
          title,
          message,
          itemName,
          deleteUrl,
          okLabel
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
