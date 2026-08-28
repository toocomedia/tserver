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
  const auditSummary = document.getElementById("audit-summary-box");
  const auditIssues = document.getElementById("audit-issues-list");

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

  // Run Test Action
  if (runBtn) {
    runBtn.addEventListener("click", async function () {
      const appSlug = appSelect ? appSelect.value : "";
      const customTarget = customInput ? customInput.value.trim() : "";
      const offline = offlineCheck ? offlineCheck.checked : false;

      runBtn.disabled = true;
      btnText.textContent = "Running AI Test (Dry Run)...";
      durationEl.textContent = "";
      verdictEl.innerHTML = "";
      timelineEl.innerHTML = '<div style="padding: 12px; font-size: 12px; color: #93c5fd;">Inspecting application and generating plan...</div>';

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
          }),
        });

        const data = await res.json();
        if (!res.ok || data.status === "error") {
          throw new Error(data.message || "Failed to execute AI test run.");
        }

        // 1. Duration & Verdict
        durationEl.textContent = `${data.duration_ms}ms · ${data.provider_name}`;
        const verdict = data.validation ? data.validation.verdict : "FAIL";
        verdictEl.innerHTML = `<span class="verdict-badge ${verdict}">${verdict}</span>`;

        // 2. Render Timeline
        if (Array.isArray(data.activities) && data.activities.length > 0) {
          timelineEl.innerHTML = data.activities.map(function (act) {
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
        } else {
          timelineEl.innerHTML = '<div style="padding: 12px; font-size: 12px; color: #9ca3af;">Deterministic offline plan synthesis completed.</div>';
        }

        // 3. Tab Contents
        codeCompose.textContent = data.compose_yaml || "# (No Compose YAML generated)";
        codeJson.textContent = JSON.stringify(data.plan_data || {}, null, 2);
        codeRaw.textContent = data.report_text || "No report generated.";

        // 4. Audit & Fixes
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
