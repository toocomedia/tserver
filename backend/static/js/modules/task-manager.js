/**
 * task-manager.js — Unified Global Background Task Manager Client
 * Handles real-time polling, header indicator state, and right-side drawer rendering.
 */
(function () {
  'use strict';

  let activeTab = 'active';
  let pollInterval = null;
  let activeTasksCache = [];
  let historyTasksCache = [];
  let isDrawerOpen = false;
  const openLogTaskIds = new Set();

  const getBackdrop = () => document.getElementById('task-drawer-backdrop');
  const getHeaderBtn = () => document.getElementById('header-task-btn');
  const getCountBadge = () => document.getElementById('task-drawer-count');
  const getActiveTabCount = () => document.getElementById('task-tab-active-count');
  const getBodyEl = () => document.getElementById('task-drawer-body');

  function escapeHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, (m) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[m]));
  }

  function openDrawer(tab) {
    const backdrop = getBackdrop();
    if (!backdrop) return;
    if (tab) activeTab = tab;
    backdrop.classList.remove('hidden');
    isDrawerOpen = true;
    updateTabButtons();
    fetchTasks(true);
  }

  function closeDrawer() {
    const backdrop = getBackdrop();
    if (!backdrop) return;
    backdrop.classList.add('hidden');
    isDrawerOpen = false;
  }

  function toggleDrawer() {
    if (isDrawerOpen) closeDrawer();
    else openDrawer();
  }

  function updateTabButtons() {
    document.querySelectorAll('[data-task-tab]').forEach((btn) => {
      btn.classList.toggle('is-active', btn.dataset.taskTab === activeTab);
    });
    const clearBtn = document.getElementById('task-drawer-clear-btn');
    if (clearBtn) {
      clearBtn.classList.toggle('hidden', activeTab !== 'history' || historyTasksCache.length === 0);
    }
  }

  function renderTasks() {
    const body = getBodyEl();
    if (!body) return;

    const list = activeTab === 'active' ? activeTasksCache : historyTasksCache;
    if (!list || list.length === 0) {
      const msg = activeTab === 'active' 
        ? (window._ ? window._('no_active_tasks') : 'No tasks running right now.')
        : (window._ ? window._('no_task_history') : 'No recent task history.');
      body.innerHTML = `<div class="task-drawer__empty">${escapeHtml(msg)}</div>`;
      return;
    }

    body.innerHTML = list.map((task) => {
      const statusTone = task.status === 'succeeded' ? 'succeeded' : task.status === 'failed' ? 'failed' : task.status === 'cancelled' ? 'cancelled' : 'running';
      const isRunning = task.status === 'running';
      const isLogOpen = openLogTaskIds.has(task.id);
      const logs = (task.logs || []).join('\n');
      
      return `
        <div class="task-item" data-task-id="${escapeHtml(task.id)}">
          <div class="task-item__top">
            <div class="task-item__info">
              <h4 class="task-item__title">${escapeHtml(task.label || task.id)}</h4>
              <div class="task-item__meta">
                <span class="task-item__status-pill task-item__status-pill--${statusTone}">${escapeHtml(task.status)}</span>
                <span>${task.elapsed_seconds || 0}s elapsed</span>
              </div>
            </div>
            ${isRunning && task.can_cancel ? `
              <button type="button" class="task-item__cancel-btn" onclick="window.cancelTask('${escapeHtml(task.id)}')">Cancel</button>
            ` : ''}
          </div>

          ${isRunning ? `
            <div class="task-item__progress">
              <div class="task-item__progress-bar ${task.progress ? '' : 'task-item__progress-bar--indeterminate'}" style="width: ${task.progress || 40}%"></div>
            </div>
          ` : ''}

          <div class="task-item__footer">
            ${logs ? `
              <button type="button" class="task-item__log-btn" onclick="window.toggleTaskLogs(this)">
                <span>Logs (${(task.logs || []).length})</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
              </button>
            ` : '<span></span>'}
          </div>

          ${logs ? `
            <div class="task-item__terminal ${isLogOpen ? 'is-open' : ''}">${escapeHtml(logs)}</div>
          ` : ''}
        </div>
      `;
    }).join('');

    body.querySelectorAll('.task-item__terminal.is-open').forEach((term) => {
      term.scrollTop = term.scrollHeight;
    });
  }

  function updateHeaderBadge(runningCount) {
    const btn = getHeaderBtn();
    const countEl = getCountBadge();
    const activeTabCountEl = getActiveTabCount();

    if (countEl) countEl.textContent = runningCount;
    if (activeTabCountEl) activeTabCountEl.textContent = runningCount;
    if (countEl) countEl.classList.toggle('is-active', runningCount > 0);

    if (btn) {
      btn.classList.toggle('is-active', runningCount > 0);
      const textEl = btn.querySelector('.header-task-btn__text');
      if (textEl) {
        textEl.textContent = runningCount > 0 ? `${runningCount} Running` : 'Tasks';
      }
    }
  }

  async function fetchTasks(forceFull = false) {
    try {
      const url = (forceFull || isDrawerOpen) ? '/api/tasks' : '/api/tasks/active';
      const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) return;
      const data = await res.json();

      const prevActiveCount = activeTasksCache.length;
      activeTasksCache = data.active || [];
      if (data.history) historyTasksCache = data.history || [];

      const running = activeTasksCache.filter((t) => t.status === 'running');
      updateHeaderBadge(running.length);

      // Broadcast completed event if active tasks decreased
      if (prevActiveCount > 0 && activeTasksCache.length < prevActiveCount) {
        document.dispatchEvent(new CustomEvent('task:completed'));
      }

      if (isDrawerOpen) {
        renderTasks();
      }

      // Concurrency lock broadcast
      if (data.locks) {
        document.dispatchEvent(new CustomEvent('task:locks-updated', { detail: data.locks }));
      }

      adjustPolling(running.length > 0);
    } catch (e) {
      console.debug('Task poll error:', e);
    }
  }

  function adjustPolling(hasRunning) {
    const targetInterval = isDrawerOpen ? 1200 : (hasRunning ? 1800 : 15000);
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(() => fetchTasks(false), targetInterval);
  }

  // Global functions for inline DOM actions
  window.openTaskDrawer = openDrawer;
  window.closeTaskDrawer = closeDrawer;
  window.toggleTaskDrawer = toggleDrawer;
  window.refreshTasks = () => fetchTasks(true);

  window.clearTaskHistory = async function () {
    try {
      const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
      const res = await fetch('/api/tasks/clear-history', {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf }
      });
      if (res.ok) {
        historyTasksCache = [];
        if (window.toast) window.toast(window._ ? window._('history_cleared') : 'Task history cleared', 'success');
        updateTabButtons();
        renderTasks();
      }
    } catch (e) {
      console.error(e);
    }
  };

  window.cancelTask = async function (taskId) {
    if (!confirm('Stop this running task?')) return;
    try {
      const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
      const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf }
      });
      if (res.ok) {
        if (window.toast) window.toast('Task cancelled', 'warning');
        fetchTasks(true);
      }
    } catch (e) {
      console.error(e);
    }
  };

  window.toggleTaskLogs = function (btn) {
    const item = btn.closest('.task-item');
    if (!item) return;
    const taskId = item.dataset.taskId;
    const term = item.querySelector('.task-item__terminal');
    if (!term || !taskId) return;

    if (openLogTaskIds.has(taskId)) {
      openLogTaskIds.delete(taskId);
      term.classList.remove('is-open');
    } else {
      openLogTaskIds.add(taskId);
      term.classList.add('is-open');
      term.scrollTop = term.scrollHeight;
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    // Backdrop click
    const backdrop = getBackdrop();
    if (backdrop) {
      backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeDrawer();
      });
    }

    // Close button
    document.getElementById('task-drawer-close-btn')?.addEventListener('click', closeDrawer);

    // Clear history button
    document.getElementById('task-drawer-clear-btn')?.addEventListener('click', window.clearTaskHistory);

    // Header task button click
    getHeaderBtn()?.addEventListener('click', toggleDrawer);

    // Tab switching
    document.querySelectorAll('[data-task-tab]').forEach((tabBtn) => {
      tabBtn.addEventListener('click', () => {
        activeTab = tabBtn.dataset.taskTab;
        updateTabButtons();
        renderTasks();
        fetchTasks(true);
      });
    });

    // Escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && isDrawerOpen) closeDrawer();
    });

    // Initial check
    fetchTasks(false);
    adjustPolling(false);
  });
})();
