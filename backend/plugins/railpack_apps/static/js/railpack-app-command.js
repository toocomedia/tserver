// railpack-app-command.js — Simple in-browser container command runner
document.addEventListener('DOMContentLoaded', () => {
  const runner = document.querySelector('[data-app-command-runner]');
  if (!runner) return;

  const appId = runner.dataset.appId;
  const form = runner.querySelector('[data-command-form]');
  const input = runner.querySelector('[data-command-input]');
  const submitBtn = runner.querySelector('[data-command-submit-btn]');
  const output = runner.querySelector('[data-terminal-output]');
  const containerSelect = runner.querySelector('[data-command-container-select]');
  const singleContainerEl = runner.querySelector('[data-command-single-container]');
  const clearBtn = runner.querySelector('[data-terminal-clear]');

  const csrfToken = document.querySelector('[name="csrf_token"]')?.value
    || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

  const history = [];
  let historyIdx = -1;

  function getContainer() {
    return containerSelect?.value || singleContainerEl?.getAttribute('data-command-single-container') || singleContainerEl?.textContent.trim() || '';
  }

  async function runCommand(cmd) {
    const text = (cmd || input?.value || '').trim();
    if (!text || !output) return;

    if (input) input.value = '';
    history.push(text);
    historyIdx = -1;

    if (submitBtn) submitBtn.disabled = true;
    if (input) input.disabled = true;

    output.textContent += `\n$ ${text}\n`;
    output.scrollTop = output.scrollHeight;

    try {
      const res = await fetch(`/plugins/railpack_apps/${appId}/command/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        body: JSON.stringify({ command: text, container_name: getContainer(), timeout: 30 })
      });
      const data = await res.json();
      if (!res.ok) {
        output.textContent += `[error] ${data.detail || 'Execution failed'}\n`;
      } else {
        if (data.stdout) output.textContent += data.stdout;
        if (data.stderr) output.textContent += (data.stdout ? '\n' : '') + `[stderr] ${data.stderr}`;
        if (!data.stdout && !data.stderr) output.textContent += '[no output]\n';
        if (data.exit_code !== 0) output.textContent += `\n[exit status ${data.exit_code}]\n`;
      }
    } catch (err) {
      output.textContent += `[error] ${err.message}\n`;
    } finally {
      if (submitBtn) submitBtn.disabled = false;
      if (input) {
        input.disabled = false;
        input.focus();
      }
      output.scrollTop = output.scrollHeight;
    }
  }

  form?.addEventListener('submit', (e) => {
    e.preventDefault();
    runCommand();
  });

  input?.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowUp' && history.length) {
      e.preventDefault();
      historyIdx = historyIdx === -1 ? history.length - 1 : Math.max(0, historyIdx - 1);
      input.value = history[historyIdx] || '';
    } else if (e.key === 'ArrowDown' && historyIdx !== -1) {
      e.preventDefault();
      historyIdx = Math.min(history.length, historyIdx + 1);
      input.value = history[historyIdx] || '';
    }
  });

  document.querySelectorAll('[data-quick-command]').forEach(btn => {
    btn.addEventListener('click', () => runCommand(btn.dataset.quickCommand));
  });

  document.querySelectorAll('[data-run-app-command]').forEach(btn => {
    btn.addEventListener('click', () => {
      let cmd = (btn.dataset.runAppCommand || '').replace(/^docker\s+exec\s+(?:-[a-zA-Z0-9]+\s+)*[^\s]+\s+/, '');
      window.activateAppTab?.('command');
      if (input) {
        input.value = cmd;
        input.focus();
      }
    });
  });

  clearBtn?.addEventListener('click', () => {
    output.textContent = `# Terminal cleared\n`;
  });
});
