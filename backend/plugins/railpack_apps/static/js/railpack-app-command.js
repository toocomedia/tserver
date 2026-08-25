// railpack-app-command.js — In-browser command runner for App Engine containers
document.addEventListener('DOMContentLoaded', () => {
  const runner = document.querySelector('[data-app-command-runner]');
  if (!runner) return;

  const appId = runner.dataset.appId;
  const form = runner.querySelector('[data-command-form]');
  const input = runner.querySelector('[data-command-input]');
  const submitBtn = runner.querySelector('[data-command-submit-btn]');
  const btnText = runner.querySelector('[data-command-btn-text]');
  const output = runner.querySelector('[data-terminal-output]');
  const targetDisplay = runner.querySelector('[data-terminal-target-display]');
  const containerSelect = runner.querySelector('[data-command-container-select]');
  const singleContainerEl = runner.querySelector('[data-command-single-container]');
  const copyBtn = runner.querySelector('[data-terminal-copy]');
  const clearBtn = runner.querySelector('[data-terminal-clear]');

  const csrfToken = document.querySelector('[name="csrf_token"]')?.value
    || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

  // ── History Management ──────────────────────────────────────────
  const storageKey = `app_cmd_history_${appId}`;
  let history = [];
  try {
    const saved = sessionStorage.getItem(storageKey);
    if (saved) history = JSON.parse(saved);
  } catch (_) {}
  let historyIdx = -1;
  let tempDraft = '';

  function saveHistory(cmd) {
    if (!cmd) return;
    history = history.filter(c => c !== cmd);
    history.push(cmd);
    if (history.length > 50) history.shift();
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(history));
    } catch (_) {}
    historyIdx = -1;
  }

  // ── Target Container Resolution ─────────────────────────────────
  function getSelectedContainer() {
    if (containerSelect) return containerSelect.value;
    if (singleContainerEl) return singleContainerEl.getAttribute('data-command-single-container') || singleContainerEl.textContent.trim();
    return '';
  }

  function updateTargetDisplay() {
    const cname = getSelectedContainer();
    if (targetDisplay && cname) {
      targetDisplay.textContent = `root@${cname}:/`;
    }
  }

  if (containerSelect) {
    containerSelect.addEventListener('change', updateTargetDisplay);
  }
  updateTargetDisplay();

  // ── Output Rendering ─────────────────────────────────────────────
  function escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function appendCommandExecution(data) {
    if (!output) return;

    const timeStr = new Date().toLocaleTimeString();
    const isSuccess = data.success !== false && data.exit_code === 0;
    const badgeColor = isSuccess ? '#10b981' : '#ef4444';
    const badgeText = isSuccess ? `Exit: ${data.exit_code}` : `Exit: ${data.exit_code || 'Err'}`;
    const durationText = data.duration_ms !== undefined ? `${data.duration_ms}ms` : '';

    const entryDiv = document.createElement('div');
    entryDiv.style.marginBottom = '14px';

    const headerHtml = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; padding-bottom: 3px; border-bottom: 1px dashed rgba(255,255,255,0.1); font-size: 11.5px; color: #94a3b8;">
        <span>
          <span style="color: #64748b;">[${timeStr}]</span>
          <span style="color: #38bdf8; font-weight: 600;">root@${escapeHtml(data.container || getSelectedContainer())}:$</span>
          <strong style="color: #f8fafc; margin-left: 4px;">${escapeHtml(data.command)}</strong>
        </span>
        <span style="display: flex; gap: 8px; align-items: center;">
          ${durationText ? `<span style="font-size: 10px; color: #64748b;">${durationText}</span>` : ''}
          <span style="background: ${badgeColor}22; color: ${badgeColor}; border: 1px solid ${badgeColor}44; padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 600;">
            ${badgeText}
          </span>
        </span>
      </div>
    `;

    let stdoutHtml = '';
    if (data.stdout && data.stdout.trim()) {
      stdoutHtml = `<div style="color: #f1f5f9; white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;">${escapeHtml(data.stdout)}</div>`;
    }

    let stderrHtml = '';
    if (data.stderr && data.stderr.trim()) {
      stderrHtml = `<div style="color: #f87171; white-space: pre-wrap; margin-top: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;">${escapeHtml(data.stderr)}</div>`;
    }

    if (!stdoutHtml && !stderrHtml) {
      stdoutHtml = `<div style="color: #64748b; font-style: italic;">[Command executed with no output returned]</div>`;
    }

    entryDiv.innerHTML = headerHtml + stdoutHtml + stderrHtml;
    output.appendChild(entryDiv);
    output.scrollTop = output.scrollHeight;
  }

  // ── Command Execution ───────────────────────────────────────────
  async function runCommand(cmd) {
    const commandToRun = (cmd || (input ? input.value : '')).trim();
    if (!commandToRun) return;

    saveHistory(commandToRun);
    if (input) input.value = '';

    const containerName = getSelectedContainer();

    // Set UI to running state
    if (submitBtn) submitBtn.disabled = true;
    if (input) input.disabled = true;
    if (btnText) btnText.textContent = 'Running...';

    try {
      const response = await fetch(`/plugins/railpack_apps/${appId}/command/run`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken,
          Accept: 'application/json',
        },
        body: JSON.stringify({
          command: commandToRun,
          container_name: containerName,
          timeout: 30,
        }),
      });

      const data = await response.json();
      if (!response.ok) {
        appendCommandExecution({
          command: commandToRun,
          container: containerName,
          exit_code: response.status,
          success: false,
          stderr: data.detail || 'Failed to execute command inside container.',
        });
      } else {
        appendCommandExecution(data);
      }
    } catch (err) {
      appendCommandExecution({
        command: commandToRun,
        container: containerName,
        exit_code: 1,
        success: false,
        stderr: `Network/Client error: ${err.message}`,
      });
    } finally {
      if (submitBtn) submitBtn.disabled = false;
      if (input) {
        input.disabled = false;
        input.focus();
      }
      if (btnText) btnText.textContent = 'Run';
    }
  }

  // Form submit
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      runCommand();
    });
  }

  // Keyboard navigation for history (Up / Down arrows)
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowUp') {
        if (!history.length) return;
        e.preventDefault();
        if (historyIdx === -1) {
          tempDraft = input.value;
          historyIdx = history.length - 1;
        } else if (historyIdx > 0) {
          historyIdx--;
        }
        input.value = history[historyIdx] || '';
      } else if (e.key === 'ArrowDown') {
        if (historyIdx === -1) return;
        e.preventDefault();
        if (historyIdx < history.length - 1) {
          historyIdx++;
          input.value = history[historyIdx] || '';
        } else {
          historyIdx = -1;
          input.value = tempDraft;
        }
      }
    });
  }

  // ── Quick Command Chips ──────────────────────────────────────────
  document.querySelectorAll('[data-quick-command]').forEach(btn => {
    btn.addEventListener('click', () => {
      const cmd = btn.getAttribute('data-quick-command');
      if (!cmd) return;
      if (input) {
        input.value = cmd;
        input.focus();
      }
      runCommand(cmd);
    });
  });

  // ── Run in App Triggers from Other Tabs (e.g. Documentation) ────
  document.querySelectorAll('[data-run-app-command]').forEach(btn => {
    btn.addEventListener('click', () => {
      let cmd = btn.getAttribute('data-run-app-command') || '';
      if (!cmd) return;

      // Strip "docker exec -it <container>" prefix if present
      cmd = cmd.replace(/^docker\s+exec\s+(?:-[a-zA-Z0-9]+\s+)*[^\s]+\s+/, '');

      // Switch to command tab
      if (typeof window.activateAppTab === 'function') {
        window.activateAppTab('command');
      } else {
        const commandTabBtn = document.querySelector('[data-app-tab="command"]');
        if (commandTabBtn) commandTabBtn.click();
      }

      if (input) {
        input.value = cmd;
        input.focus();
      }
    });
  });

  // ── Terminal Actions (Clear & Copy) ─────────────────────────────
  if (clearBtn && output) {
    clearBtn.addEventListener('click', () => {
      output.innerHTML = `
        <span style="color: #64748b;"># Container Command Console (Scoped to active app container)</span>\n<span style="color: #64748b;"># Terminal cleared.</span>
      `;
    });
  }

  if (copyBtn && output) {
    copyBtn.addEventListener('click', async () => {
      const text = output.innerText || output.textContent;
      try {
        await navigator.clipboard.writeText(text);
        const originalText = copyBtn.textContent;
        copyBtn.textContent = 'Copied!';
        setTimeout(() => {
          copyBtn.textContent = originalText;
        }, 1500);
      } catch (_) {
        window.prompt('Copy output:', text);
      }
    });
  }
});
