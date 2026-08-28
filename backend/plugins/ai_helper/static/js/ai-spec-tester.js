/**
 * ai-spec-tester.js — Client logic for AI Spec & Plan Dev Tester Playground.
 */
document.addEventListener("DOMContentLoaded", function () {
  const appSelect = document.getElementById("app-select");
  const customInput = document.getElementById("custom-target-input");
  const offlineCheck = document.getElementById("offline-checkbox");
  const runBtn = document.getElementById("btn-run-test");
  const btnText = document.getElementById("btn-text");
  const timelineEl = document.getElementById("activity-timeline");
  const durationEl = document.getElementById("run-duration");
  const verdictEl = document.getElementById("verdict-container");
  const codeCompose = document.getElementById("code-compose");
  const codeJson = document.getElementById("code-json");
  const codeRaw = document.getElementById("code-raw");
  const codeRawLog = document.getElementById("code-rawlog");
  const auditSummary = document.getElementById("audit-summary-box");
  const auditIssues = document.getElementById("audit-issues-list");
  const providerSelect = document.getElementById("provider-select");
  const modelSelect = document.getElementById("model-select");
  const modelCustomInput = document.getElementById("model-custom-input");
  const providersDataScript = document.getElementById("providers-data");

  const providers = JSON.parse(providersDataScript ? providersDataScript.textContent : "[]");
  const DEFAULT_MODELS = {
    anthropic: ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
    openai_compatible: ["gpt-4o", "gpt-4o-mini", "o3-mini", "o1", "deepseek-chat", "deepseek-reasoner"],
    ollama: ["llama3.2", "llama3.1", "qwen2.5-coder", "deepseek-r1", "mistral"],
  };

  function populateModels() {
    if (!modelSelect || !providerSelect) return;
    const selectedProviderId = parseInt(providerSelect.value, 10);
    const provider = providers.find(function (p) { return p.id === selectedProviderId; });
    if (!provider) return;

    const modelSet = new Set();
    if (provider.model_name) modelSet.add(provider.model_name.trim());
    if (Array.isArray(provider.models)) {
      provider.models.forEach(function (m) { if (m && m.trim()) modelSet.add(m.trim()); });
    }
    const common = DEFAULT_MODELS[provider.type] || DEFAULT_MODELS.openai_compatible;
    common.forEach(function (m) { modelSet.add(m); });

    const modelList = Array.from(modelSet);
    modelSelect.innerHTML = modelList.map(function (m) {
      const isSelected = m === provider.model_name ? "selected" : "";
      return `<option value="${escapeHtml(m)}" ${isSelected}>${escapeHtml(m)}</option>`;
    }).join("") + '<option value="__custom__">Custom model (type below)...</option>';

    toggleCustomInput();
  }

  function toggleCustomInput() {
    if (!modelSelect || !modelCustomInput) return;
    if (modelSelect.value === "__custom__") {
      modelCustomInput.style.display = "block";
      modelCustomInput.focus();
    } else {
      modelCustomInput.style.display = "none";
    }
  }

  if (providerSelect) {
    providerSelect.addEventListener("change", populateModels);
  }
  if (modelSelect) {
    modelSelect.addEventListener("change", toggleCustomInput);
  }
  populateModels();

  // Tab switching
  const tabBtns = document.querySelectorAll(".dev-tab-btn");
  tabBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      tabBtns.forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      const target = btn.getAttribute("data-tab");
      document.querySelectorAll(".tab-pane").forEach(function (pane) {
        pane.style.display = "none";
      });
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
    timelineEl.innerHTML = activities.map(function (act) {
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

  // Run Test Action with Streaming Reader
  if (runBtn) {
    runBtn.addEventListener("click", async function () {
      const appSlug = appSelect ? appSelect.value : "";
      const customTarget = customInput ? customInput.value.trim() : "";
      const offline = offlineCheck ? offlineCheck.checked : false;
      const providerId = providerSelect && providerSelect.value ? parseInt(providerSelect.value, 10) : null;
      const selectedModel = (modelSelect && modelSelect.value === "__custom__")
        ? (modelCustomInput ? modelCustomInput.value.trim() : "")
        : (modelSelect ? modelSelect.value : "");

      runBtn.disabled = true;
      btnText.textContent = "Running AI Test (Streaming)...";
      durationEl.textContent = "";
      verdictEl.innerHTML = "";
      timelineEl.innerHTML = '<div style="padding: 12px; font-size: 12px; color: #93c5fd;">Connecting to server stream...</div>';

      try {
        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const csrfInput = document.querySelector('input[name="csrf_token"]');
        const csrfToken = (csrfMeta && csrfMeta.getAttribute("content")) || (csrfInput && csrfInput.value) || "";

        const res = await fetch("/plugins/ai_helper/api/spec-tester/run", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
          },
          body: JSON.stringify({
            app_slug: appSlug,
            custom_target: customTarget,
            offline: offline,
            provider_id: providerId,
            model_name: selectedModel || null,
          }),
        });

        if (!res.ok) {
          throw new Error(`HTTP ${res.status}: Failed to connect to test runner.`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let finalData = null;
        let activitiesList = [];

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split("\n");
          buffer = lines.pop();

          for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            if (!line || line.startsWith(":")) continue; // heartbeat ping
            if (line.startsWith("data:")) {
              const rawJson = line.slice(5).trim();
              if (!rawJson) continue;
              try {
                const event = JSON.parse(rawJson);
                if (event.type === "activity") {
                  activitiesList.push(event);
                  renderActivities(activitiesList);
                } else if (event.type === "final") {
                  finalData = event;
                } else if (event.type === "error") {
                  throw new Error(event.message || "Execution error");
                }
              } catch (parseErr) {
                // Ignore chunk parse error
              }
            }
          }
        }

        if (!finalData) {
          throw new Error("Stream closed before receiving final test results.");
        }

        const data = finalData;

        // 1. Duration & Verdict
        durationEl.textContent = `${data.duration_ms}ms · ${data.provider_name} (${data.model_name})`;
        const verdict = data.validation ? data.validation.verdict : "FAIL";
        verdictEl.innerHTML = `<span class="verdict-badge ${verdict}">${verdict}</span>`;

        // 2. Tab Contents
        codeCompose.textContent = data.compose_yaml || "# (No Compose YAML generated)";
        codeJson.textContent = JSON.stringify(data.plan_data || {}, null, 2);
        codeRaw.textContent = data.report_text || "No report generated.";
        if (codeRawLog) codeRawLog.textContent = data.raw_log || "(No raw execution log recorded.)";

        // 3. Audit & Fixes
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
          auditIssues.innerHTML = val.issues.map(function (issue) {
            return `
              <div class="issue-card ${issue.severity}">
                <div style="font-weight: 600;">[${issue.severity}] ${escapeHtml(issue.field)}</div>
                <div style="margin-top: 3px; color: #cbd5e1;">${escapeHtml(issue.message)}</div>
                ${issue.fix_advice ? `<div class="fix-box">↳ FIX HERE: ${escapeHtml(issue.fix_advice)}</div>` : ""}
              </div>
            `;
          }).join("");
        }

      } catch (err) {
        timelineEl.innerHTML = `<div style="padding: 12px; color: #ef4444; font-size: 12px;">Error: ${escapeHtml(err.message)}</div>`;
        verdictEl.innerHTML = '<span class="verdict-badge FAIL">FAIL</span>';
      } finally {
        runBtn.disabled = false;
        btnText.textContent = "Run AI Test (Dry Run)";
      }
    });
  }
});

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function copyCode(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const text = el.textContent || "";
  navigator.clipboard.writeText(text).then(function () {
    if (window.toast) {
      window.toast("Copied to clipboard!", "success");
    } else {
      alert("Copied to clipboard!");
    }
  }).catch(function (err) {
    console.error("Clipboard copy error:", err);
  });
}
