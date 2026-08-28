/**
 * ai-spec-tester.js — Client logic for AI Spec & Plan Dev Tester Playground.
 */
let lastYaml = "";
let lastJson = "";
let currentTemplateFormat = "yaml";

document.addEventListener("DOMContentLoaded", () => {
  const appSelect = document.getElementById("app-select");
  const customInput = document.getElementById("custom-target-input");
  const offlineCheck = document.getElementById("offline-checkbox");
  const runBtn = document.getElementById("btn-run-test");
  const btnText = document.getElementById("btn-text");
  const timelineEl = document.getElementById("activity-timeline");
  const durationEl = document.getElementById("run-duration");
  const verdictEl = document.getElementById("verdict-container");
  const codeTemplate = document.getElementById("code-template");
  const btnTplYaml = document.getElementById("btn-tpl-yaml");
  const btnTplJson = document.getElementById("btn-tpl-json");
  const codeRawLog = document.getElementById("code-rawlog");
  const rawlogLiveStatus = document.getElementById("rawlog-live-status");
  const auditSummary = document.getElementById("audit-summary-box");
  const auditIssues = document.getElementById("audit-issues-list");
  const providerSelect = document.getElementById("provider-select");
  const modelSelect = document.getElementById("model-select");
  const modelCustomInput = document.getElementById("model-custom-input");
  const providersDataScript = document.getElementById("providers-data");
  const telemetryBar = document.getElementById("telemetry-bar");
  const statTotal = document.getElementById("stat-total-time");
  const statTurn1 = document.getElementById("stat-turn1-time");
  const statTurn2 = document.getElementById("stat-turn2-time");
  const statAudit = document.getElementById("stat-audit-time");
  const statStage = document.getElementById("stat-live-stage");

  const providers = JSON.parse(providersDataScript ? providersDataScript.textContent : "[]");
  const DEFAULT_MODELS = {
    anthropic: ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"],
    openai_compatible: ["gpt-4o", "gpt-4o-mini", "o3-mini", "deepseek-chat"],
    ollama: ["llama3.2", "qwen2.5-coder", "deepseek-r1"],
  };

  function setTelemetry(total, t1, t2, audit, stage) {
    if (statTotal) statTotal.textContent = total;
    if (statTurn1) statTurn1.textContent = t1;
    if (statTurn2) statTurn2.textContent = t2;
    if (statAudit) statAudit.textContent = audit;
    if (statStage) statStage.textContent = stage;
  }

  function updateTemplateView() {
    if (!codeTemplate) return;
    if (currentTemplateFormat === "yaml") {
      codeTemplate.textContent = lastYaml || "# Run a test to generate the App Template (YAML)";
      if (btnTplYaml) btnTplYaml.className = "btn btn--sm btn--primary";
      if (btnTplJson) btnTplJson.className = "btn btn--sm btn--secondary";
    } else {
      codeTemplate.textContent = lastJson || "{}";
      if (btnTplYaml) btnTplYaml.className = "btn btn--sm btn--secondary";
      if (btnTplJson) btnTplJson.className = "btn btn--sm btn--primary";
    }
  }

  if (btnTplYaml) btnTplYaml.addEventListener("click", () => { currentTemplateFormat = "yaml"; updateTemplateView(); });
  if (btnTplJson) btnTplJson.addEventListener("click", () => { currentTemplateFormat = "json"; updateTemplateView(); });

  function populateModels() {
    if (!modelSelect || !providerSelect) return;
    const provider = providers.find(p => p.id === parseInt(providerSelect.value, 10));
    if (!provider) return;

    const modelSet = new Set();
    if (provider.model_name) modelSet.add(provider.model_name.trim());
    if (Array.isArray(provider.models)) provider.models.forEach(m => m && modelSet.add(m.trim()));
    const common = DEFAULT_MODELS[provider.type] || DEFAULT_MODELS.openai_compatible;
    common.forEach(m => modelSet.add(m));

    modelSelect.innerHTML = Array.from(modelSet).map(m => {
      const isSelected = m === provider.model_name ? "selected" : "";
      return `<option value="${escapeHtml(m)}" ${isSelected}>${escapeHtml(m)}</option>`;
    }).join("") + '<option value="__custom__">Custom model (type below)...</option>';

    toggleCustomInput();
  }

  function toggleCustomInput() {
    if (!modelSelect || !modelCustomInput) return;
    const isCustom = modelSelect.value === "__custom__";
    modelCustomInput.style.display = isCustom ? "block" : "none";
    if (isCustom) modelCustomInput.focus();
  }

  if (providerSelect) providerSelect.addEventListener("change", populateModels);
  if (modelSelect) modelSelect.addEventListener("change", toggleCustomInput);
  populateModels();

  // Tab switching
  const tabBtns = document.querySelectorAll(".dev-tab-btn");
  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      tabBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const target = btn.getAttribute("data-tab");
      document.querySelectorAll(".tab-pane").forEach(p => p.style.display = "none");
      const activePane = document.getElementById("tab-pane-" + target);
      if (activePane) activePane.style.display = "block";
    });
  });

  function renderActivities(activities) {
    if (!timelineEl) return;
    if (!Array.isArray(activities) || activities.length === 0) {
      timelineEl.innerHTML = '<div style="padding: 12px; font-size: 12px; color: #9ca3af;">Inspecting and generating plan...</div>';
      return;
    }
    timelineEl.innerHTML = activities.map(act => {
      const statusClass = act.status === "done" || act.status === "ok" ? "done" : (act.status === "error" ? "error" : "");
      return `
        <div class="timeline-item ${statusClass}">
          <div>
            <span class="timeline-tool">${escapeHtml(act.tool)}</span>
            <span class="timeline-time">+${act.timestamp_ms}ms</span>
          </div>
          ${act.label ? `<div style="color: #cbd5e1; margin-top: 2px;">${escapeHtml(act.label)}</div>` : ""}
          ${act.detail ? `<div style="color: #64748b; font-size: 11px; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(act.detail)}</div>` : ""}
        </div>
      `;
    }).join("");
  }

  // Run Test Action with Streaming Reader and Live Telemetry
  if (runBtn) {
    runBtn.addEventListener("click", async () => {
      const appSlug = appSelect ? appSelect.value : "";
      const customTarget = customInput ? customInput.value.trim() : "";
      const offline = offlineCheck ? offlineCheck.checked : false;
      const providerId = providerSelect && providerSelect.value ? parseInt(providerSelect.value, 10) : null;
      const selectedModel = (modelSelect && modelSelect.value === "__custom__")
        ? (modelCustomInput ? modelCustomInput.value.trim() : "")
        : (modelSelect ? modelSelect.value : "");

      runBtn.disabled = true;
      btnText.textContent = "Running AI Test (Streaming)...";
      verdictEl.innerHTML = "";
      timelineEl.innerHTML = '<div style="padding: 12px; font-size: 12px; color: #93c5fd;">Connecting to server stream...</div>';

      if (telemetryBar) telemetryBar.style.display = "block";
      setTelemetry("Total: 0.0s", "Turn 1: ...", "Turn 2: ...", "Audit: ...", "Connecting to provider...");
      if (rawlogLiveStatus) rawlogLiveStatus.textContent = "● Live Streaming AI output...";
      if (codeRawLog) codeRawLog.textContent = `=== LIVE EXECUTION STREAM INITIALIZING ===\nTarget: ${customTarget || appSlug}\n`;

      const startTime = Date.now();
      const timerInterval = setInterval(() => {
        const sec = ((Date.now() - startTime) / 1000).toFixed(1);
        if (durationEl) durationEl.textContent = `⏱ Running: ${sec}s...`;
        if (statTotal) statTotal.textContent = `Total: ${sec}s`;
      }, 100);

      try {
        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const csrfInput = document.querySelector('input[name="csrf_token"]');
        const csrfToken = (csrfMeta && csrfMeta.getAttribute("content")) || (csrfInput && csrfInput.value) || "";

        const res = await fetch("/plugins/ai_helper/api/spec-tester/run", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
          body: JSON.stringify({
            app_slug: appSlug,
            custom_target: customTarget,
            offline: offline,
            provider_id: providerId,
            model_name: selectedModel || null,
          }),
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to connect to test runner.`);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "", finalData = null, activitiesList = [];

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop();

          for (const rawLine of lines) {
            const line = rawLine.trim();
            if (!line || line.startsWith(":") || !line.startsWith("data:")) continue;
            try {
              const event = JSON.parse(line.slice(5).trim());
              if (event.type === "status") {
                if (statStage) statStage.textContent = event.stage;
                if (codeRawLog) codeRawLog.textContent += `\n>>> [STATUS] ${event.stage} (+${event.timestamp_ms || 0}ms)\n`;
              } else if (event.type === "activity") {
                activitiesList.push(event);
                renderActivities(activitiesList);
                if (codeRawLog) codeRawLog.textContent += `  [TOOL] ${event.tool} -> ${event.status.toUpperCase()}: ${event.label} (${event.detail})\n`;
              } else if (event.type === "token") {
                if (codeRawLog) {
                  codeRawLog.textContent += event.text;
                  codeRawLog.scrollTop = codeRawLog.scrollHeight;
                }
              } else if (event.type === "final") {
                finalData = event;
              } else if (event.type === "error") {
                throw new Error(event.message || "Execution error");
              }
            } catch (err) { /* ignore chunk err */ }
          }
        }

        if (!finalData) throw new Error("Stream closed before receiving final test results.");

        const data = finalData;
        const t = data.timing || {};
        const totalSec = (data.duration_ms / 1000).toFixed(2);
        const turn1Sec = t.turn1_ms ? (t.turn1_ms / 1000).toFixed(2) : "0.00";
        const turn2Sec = t.turn2_ms ? (t.turn2_ms / 1000).toFixed(2) : "0.00";

        setTelemetry(`Total: ${totalSec}s`, `Turn 1 (Inspect & AI): ${turn1Sec}s`, `Turn 2 (Synthesis): ${turn2Sec}s`, `Audit: ${t.validation_ms || 0}ms`, `✓ Completed in ${totalSec}s`);
        if (rawlogLiveStatus) rawlogLiveStatus.textContent = `✓ Finished (${totalSec}s)`;
        durationEl.textContent = `⏱ ${totalSec}s · ${data.provider_name} (${data.model_name})`;
        const verdict = data.validation ? data.validation.verdict : "FAIL";
        verdictEl.innerHTML = `<span class="verdict-badge ${verdict}">${verdict}</span>`;

        lastYaml = data.compose_yaml || "# (No Compose YAML generated)";
        lastJson = JSON.stringify(data.plan_data || {}, null, 2);
        updateTemplateView();

        if (codeRawLog) codeRawLog.textContent = data.raw_log || "(No raw execution log recorded.)";

        const val = data.validation || {};
        auditSummary.innerHTML = `
          <div style="background: rgba(255,255,255,0.03); padding: 10px; border-radius: 6px; margin-bottom: 12px;">
            <strong>Services:</strong> ${escapeHtml((val.detected_services || []).join(", ") || "None")} &nbsp;|&nbsp;
            <strong>Internal Port:</strong> ${val.detected_port || "Unspecified"} &nbsp;|&nbsp;
            <strong>Database:</strong> ${escapeHtml(val.detected_database || "none")} &nbsp;|&nbsp;
            <strong>Errors:</strong> ${val.error_count || 0} &nbsp;|&nbsp;
            <strong>Warnings:</strong> ${val.warning_count || 0}
          </div>
        `;

        if (!val.issues || val.issues.length === 0) {
          auditIssues.innerHTML = '<div style="padding: 12px; color: #34d399; font-weight: 500;">✓ Plan conforms to all security, port, and AppSpec schema policies.</div>';
        } else {
          auditIssues.innerHTML = val.issues.map(issue => `
            <div class="issue-card ${issue.severity}">
              <div style="font-weight: 600;">[${issue.severity}] ${escapeHtml(issue.field)}</div>
              <div style="margin-top: 3px; color: #cbd5e1;">${escapeHtml(issue.message)}</div>
              ${issue.fix_advice ? `<div class="fix-box">↳ FIX HERE: ${escapeHtml(issue.fix_advice)}</div>` : ""}
            </div>
          `).join("");
        }

      } catch (err) {
        timelineEl.innerHTML = `<div style="padding: 12px; color: #ef4444; font-size: 12px;">Error: ${escapeHtml(err.message)}</div>`;
        verdictEl.innerHTML = '<span class="verdict-badge FAIL">FAIL</span>';
        if (rawlogLiveStatus) rawlogLiveStatus.textContent = "Error during test";
      } finally {
        clearInterval(timerInterval);
        runBtn.disabled = false;
        btnText.textContent = "Run AI Test (Dry Run)";
      }
    });
  }
});

function escapeHtml(str) {
  if (!str) return "";
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function copyTemplateCode() {
  const el = document.getElementById("code-template");
  if (!el) return;
  navigator.clipboard.writeText(el.textContent || "").then(() => {
    if (window.toast) window.toast("Copied template to clipboard!", "success");
    else alert("Copied template to clipboard!");
  }).catch(err => console.error("Clipboard copy error:", err));
}

function copyCode(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent || "").then(() => {
    if (window.toast) window.toast("Copied to clipboard!", "success");
    else alert("Copied to clipboard!");
  }).catch(err => console.error("Clipboard copy error:", err));
}
