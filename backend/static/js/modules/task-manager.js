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

  let isInitialPoll = true;
  let knownRunningIds = new Set();
  let knownCompletedIds = new Set();
  let isSyncingPage = false;

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
    if (tab === 'auto') {
      const running = activeTasksCache.filter((t) => t.status === 'running');
      activeTab = running.length > 0 ? 'active' : 'history';
    } else if (tab) {
      activeTab = tab;
    }
    backdrop.classList.remove('hidden');
    isDrawerOpen = true;
    updateTabButtons();
    fetchTasks(true).then(() => {
      if (tab === 'auto' || !tab) {
        const running = activeTasksCache.filter((t) => t.status === 'running');
        activeTab = running.length > 0 ? 'active' : 'history';
        updateTabButtons();
        renderTasks();
      }
    });
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
    const footer = document.getElementById('task-drawer-footer');
    if (footer) {
      footer.classList.toggle('hidden', activeTab !== 'history' || historyTasksCache.length === 0);
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
      
      let timeStr = '';
      if (task.started_at) {
        const d = new Date(task.started_at * 1000);
        timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      }
      
      return `
        <div class="task-item" data-task-id="${escapeHtml(task.id)}">
          <div class="task-item__header-row">
            <h4 class="task-item__title" title="${escapeHtml(task.label || task.id)}">${escapeHtml(task.label || task.id)}</h4>
            ${timeStr ? `<span class="task-item__time">${timeStr}</span>` : ''}
          </div>

          <div class="task-item__meta-row">
            <div class="task-item__meta">
              <span class="task-item__status-pill task-item__status-pill--${statusTone}">${escapeHtml(task.status)}</span>
              ${task.elapsed_seconds !== undefined ? `<span class="task-item__duration">${task.elapsed_seconds}s elapsed</span>` : ''}
              ${task.category ? `<span class="task-item__category">${escapeHtml(task.category)}</span>` : ''}
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

          ${logs ? `
            <div class="task-item__footer">
              <button type="button" class="task-item__log-btn" onclick="window.toggleTaskLogs(this)">
                <span>Logs (${(task.logs || []).length})</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
              </button>
            </div>
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

      activeTasksCache = data.active || [];
      if (data.history) historyTasksCache = data.history || [];

      const currentRunning = activeTasksCache.filter((t) => t.status === 'running');
      const currentRunningIds = new Set(currentRunning.map((t) => t.id));
      updateHeaderBadge(currentRunning.length);

      let completedCount = 0;

      if (!isInitialPoll) {
        // Any task that was running and is no longer running has finished
        for (const prevId of knownRunningIds) {
          if (!currentRunningIds.has(prevId)) {
            completedCount++;
          }
        }
        // Also check if new completed tasks appeared in history
        if (data.history) {
          for (const h of data.history) {
            if (h.id && !knownCompletedIds.has(h.id) && !knownRunningIds.has(h.id)) {
              completedCount++;
            }
          }
        }
      }

      // Update tracking sets
      knownRunningIds = currentRunningIds;
      if (data.history) {
        data.history.forEach((h) => { if (h.id) knownCompletedIds.add(h.id); });
      }
      activeTasksCache.forEach((t) => {
        if (t.id && (t.status === 'succeeded' || t.status === 'failed' || t.status === 'cancelled')) {
          knownCompletedIds.add(t.id);
        }
      });

      isInitialPoll = false;

      // Broadcast completed event whenever task(s) finish
      if (completedCount > 0) {
        document.dispatchEvent(new CustomEvent('task:completed', { detail: { count: completedCount } }));
      }

      if (isDrawerOpen) {
        renderTasks();
      }

      // Concurrency lock broadcast
      if (data.locks) {
        document.dispatchEvent(new CustomEvent('task:locks-updated', { detail: data.locks }));
      }

      adjustPolling(currentRunning.length > 0);
    } catch (e) {
      console.debug('Task poll error:', e);
    }
  }

  async function syncCurrentPageTable() {
    const path = window.location.pathname;
    // Skip on form/wizard creation pages
    if (path.endsWith('/create') || path.endsWith('/create/') || path.includes('/edit') || path.includes('/wizard')) {
      return;
    }

    if (isSyncingPage) return;
    isSyncingPage = true;

    try {
      const res = await fetch(window.location.href, {
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'text/html',
          'Cache-Control': 'no-cache'
        }
      });
      if (!res.ok) return;
      const html = await res.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');

      // Priority 1: Match #main-content .content__inner (covers whole page body + drawer modals seamlessly)
      const currentInner = document.querySelector('#main-content .content__inner');
      const newInner = doc.querySelector('#main-content .content__inner');

      if (currentInner && newInner) {
        currentInner.innerHTML = newInner.innerHTML;
      } else {
        // Priority 2: Fallback to table-wrap or table if custom layout
        const currentTableWrap = document.querySelector('.table-wrap, .table');
        const newTableWrap = doc.querySelector('.table-wrap, .table');
        if (currentTableWrap && newTableWrap) {
          currentTableWrap.innerHTML = newTableWrap.innerHTML;
        }
      }

      // Recreate Lucide icons if available
      if (typeof lucide !== 'undefined' && lucide.createIcons) {
        lucide.createIcons();
      }

      // Re-init lazy image skeletons
      if (typeof window.initLazyImageSkeletons === 'function') {
        window.initLazyImageSkeletons();
      }

      // Hide/remove any skeleton overlays in fresh HTML
      document.querySelectorAll('.skeleton-overlay').forEach((el) => {
        el.classList.add('is-hidden');
        el.style.display = 'none';
      });

      // Dispatch events for page-specific initialization
      document.dispatchEvent(new CustomEvent('app:init'));
      document.dispatchEvent(new CustomEvent('page:refreshed', { detail: { url: window.location.href } }));
    } catch (err) {
      console.debug('Failed to sync page table in place:', err);
    } finally {
      isSyncingPage = false;
    }
  }

  document.addEventListener('task:completed', syncCurrentPageTable);

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

    // Check if redirect requested drawer open
    let shouldAutoOpen = false;
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const flag = sessionStorage.getItem('open_task_drawer') || urlParams.get('open_tasks');
      if (flag) {
        shouldAutoOpen = true;
        sessionStorage.removeItem('open_task_drawer');
      }
    } catch (e) {}

    if (shouldAutoOpen) {
      fetchTasks(true).then(() => {
        openDrawer('auto');
      });
    } else {
      fetchTasks(false);
    }
    adjustPolling(false);
  });
})();
