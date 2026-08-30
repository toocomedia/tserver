/**
 * chat_markdown.js — Markdown & Action Tag Parser with One-Line Card Strips & Split Viewer Integration.
 */
(function () {
  "use strict";

  var EXT_MAP = {
    nginx: "nginx.conf",
    dockerfile: "Dockerfile",
    docker: "Dockerfile",
    compose: "docker-compose.yml",
    yaml: "config.yaml",
    yml: "config.yml",
    json: "config.json",
    bash: "script.sh",
    sh: "script.sh",
    shell: "script.sh",
    python: "main.py",
    py: "main.py",
    php: "index.php",
    javascript: "app.js",
    js: "app.js",
    typescript: "app.ts",
    ts: "app.ts",
    sql: "query.sql",
    html: "index.html",
    css: "style.css",
    ini: "config.ini",
    env: ".env",
    xml: "data.xml",
    markdown: "README.md",
    md: "README.md",
  };

  var AiHelperMarkdown = {
    _buildDirectoryCard: function (lines) {
      if (!Array.isArray(lines)) {
        lines = (lines || "").split("\n");
      }
      var folders = [];
      var files = [];
      var plainNames = [];

      var FOLDER_SVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>';
      var FILE_SVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>';

      lines.forEach(function (rawLine) {
        var line = (rawLine || "").trim();
        if (!line) return;

        // Strip leading markdown bullets / dashes / numbers
        line = line.replace(/^[-*+]\s+/, "").replace(/^\d+\.\s+/, "").trim();

        // Check if folder or file
        var isFolder = false;
        if (line.indexOf("📁") !== -1 || line.indexOf("🗂️") !== -1 || line.indexOf("📂") !== -1 || line.indexOf("[DIR]") !== -1) {
          isFolder = true;
        }

        // Clean out emojis, code tags, html tags, backticks
        var clean = line
          .replace(/[\u{1F300}-\u{1F9FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}]/gu, "")
          .replace(/\[(?:DIR|FILE)\]/gi, "")
          .replace(/&lt;\/?code&gt;/gi, "")
          .replace(/<\/?code>/gi, "")
          .replace(/`/g, "")
          .trim();

        // Extract metadata if in parens e.g. (76 B, sensitive — masked) or (921 B)
        var metaMatch = clean.match(/\(([^)]+)\)$/);
        var meta = "";
        if (metaMatch) {
          meta = metaMatch[1].trim();
          clean = clean.substring(0, metaMatch.index).trim();
        }

        // If ends with slash, it's a folder
        if (clean.endsWith("/")) {
          isFolder = true;
        }

        if (!clean) return;

        if (isFolder && !clean.endsWith("/")) {
          clean += "/";
        }

        plainNames.push(clean);

        if (isFolder) {
          folders.push({ name: clean, meta: meta });
        } else {
          files.push({ name: clean, meta: meta });
        }
      });

      var allItems = [];
      folders.forEach(function (f) { allItems.push({ type: "folder", name: f.name, meta: f.meta }); });
      files.forEach(function (f) { allItems.push({ type: "file", name: f.name, meta: f.meta }); });

      if (allItems.length === 0) return "";

      var totalCount = allItems.length;
      var countLabel = "";
      if (folders.length > 0 && files.length > 0) {
        countLabel = folders.length + " dir" + (folders.length > 1 ? "s" : "") + ", " + files.length + " file" + (files.length > 1 ? "s" : "");
      } else if (folders.length > 0) {
        countLabel = folders.length + " folder" + (folders.length > 1 ? "s" : "");
      } else {
        countLabel = files.length + " file" + (files.length > 1 ? "s" : "");
      }

      var encodedNames = encodeURIComponent(plainNames.join("\n"));

      var rowsHtml = allItems.map(function (item, idx) {
        var isHidden = idx >= 5;
        var iconSvg = item.type === "folder" ? FOLDER_SVG : FILE_SVG;
        var metaBadge = "";
        if (item.meta) {
          var cleanMeta = item.meta.replace(/,\s*sensitive\s*—\s*/gi, " · ").replace(/,\s*/g, " · ");
          var isMasked = item.meta.toLowerCase().indexOf("mask") !== -1;
          metaBadge = '<span class="ai-file-meta' + (isMasked ? ' ai-file-meta--masked' : '') + '">' + cleanMeta + '</span>';
        }
        return (
          '<div class="ai-file-row ai-file-row--' + item.type + (isHidden ? ' ai-file-row--hidden' : '') + '" data-name="' + item.name + '" title="' + item.name + '">' +
          '  <span class="ai-file-icon">' + iconSvg + '</span>' +
          '  <span class="ai-file-name">' + item.name + '</span>' +
          metaBadge +
          '</div>'
        );
      }).join("");

      var expandBtnHtml = "";
      if (totalCount > 5) {
        var hiddenCount = totalCount - 5;
        expandBtnHtml = '<button type="button" class="ai-file-expand-btn" data-hidden-count="' + hiddenCount + '">Show ' + hiddenCount + ' more items ▾</button>';
      }

      return [
        '<div class="ai-file-tree-card" data-raw-names="' + encodedNames + '">',
        '  <div class="ai-file-tree-header">',
        '    <div class="ai-file-tree-header-left">',
        '      <span class="ai-file-tree-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg></span>',
        '      <span class="ai-file-tree-title">Directory Contents</span>',
        '      <span class="ai-file-tree-count">' + countLabel + '</span>',
        '    </div>',
        '    <button type="button" class="ai-file-tree-copy-btn" title="Copy file names"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy</button>',
        '  </div>',
        '  <div class="ai-file-tree-body">',
        '    <div class="ai-file-tree-list">' + rowsHtml + '</div>' + expandBtnHtml,
        '  </div>',
        '</div>',
      ].join("\n");
    },

    _escape: function (str) {
      return String(str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    },

    _renderSetupAnswers: function (text) {
      var lines = text.split("\n").slice(1);
      var items = [];
      var self = this;
      lines.forEach(function (line) {
        var parts = line.split(":");
        if (parts.length >= 2) {
          var k = parts[0].trim().replace(/^provider\./, "").replace(/_/g, " ");
          var v = parts.slice(1).join(":").trim();
          if (v && v !== "[skip]") {
            if (v === "compose_stack") v = "Docker Compose";
            else if (v === "git_build") v = "Railpack";
            else if (v === "docker") v = "Container";
            items.push('<div style="display:flex;gap:6px;font-size:11.5px;line-height:1.4;"><span style="color:var(--color-muted,#94a3b8);text-transform:capitalize;">' + self._escape(k) + ':</span><span style="font-weight:500;">' + self._escape(v) + '</span></div>');
          }
        }
      });
      return [
        '<div style="background:var(--color-surface,#18181b);border:1px solid var(--color-line,rgba(255,255,255,.08));border-radius:6px;padding:8px 10px;margin:2px 0;">',
        '  <div style="font-size:10.5px;font-weight:600;color:var(--color-muted,#a1a1aa);text-transform:uppercase;letter-spacing:0.4px;margin-bottom:4px;">Configuration Confirmed</div>',
        '  <div>' + items.join("") + '</div>',
        '</div>',
      ].join("\n");
    },

    render: function (text) {
      if (!text) return "";
      if (text.indexOf("Setup interview answers:") === 0) {
        return this._renderSetupAnswers(text);
      }
      var thinkMatch = text.match(/<think>([\s\S]*?)(?:<\/think>|$)/i);
      var thinkHtml = "";
      if (thinkMatch && thinkMatch[1].trim()) {
        var isStreamingThinking = text.indexOf("</think>") === -1 && text.indexOf("<think>") !== -1;
        var rawThink = thinkMatch[1].trim();
        var escapedThink = rawThink.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        thinkHtml = '<div class="ai-thought-box' + (isStreamingThinking ? ' is-thinking is-expanded' : '') + '" data-thought-box>' +
          '<button type="button" class="ai-thought-toggle" data-thought-toggle>' +
          '<span class="ai-thought-icon">' + (isStreamingThinking ? '<span class="ai-activity-spinner"></span>' : '🧠') + '</span>' +
          '<span class="ai-thought-label">' + (isStreamingThinking ? 'Reasoning...' : 'Reasoning') + '</span>' +
          '<span class="ai-thought-chevron">' + (isStreamingThinking ? '▴' : '▾') + '</span>' +
          '</button>' +
          '<div class="ai-thought-content">' + escapedThink + '</div>' +
          '</div>';
      }
      var mainText = text.replace(/<think>[\s\S]*?(?:<\/think>|$)/gi, "").trim();
      return thinkHtml + this._renderMarkdownCore(mainText);
    },

    _renderMarkdownCore: function (text) {
      if (!text) return "";
      var self = this;

      // Escape HTML entities
      var escaped = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

      // Filter raw XML & DeepSeek DSML pseudo tool calls if emitted in text
      escaped = escaped.replace(/&lt;[｜|]{1,2}DSML[｜|]{1,2}[\s\S]*?(?:&lt;\/[｜|]{1,2}DSML[｜|]{1,2}[^&gt;]*&gt;|$)/gi, "");
      escaped = escaped.replace(/&lt;[｜|][\s\S]*?[｜|]&gt;/gi, "");
      escaped = escaped.replace(/&lt;tool_call&gt;[\s\S]*?(?:&lt;\/tool_call&gt;|$)/gi, "");
      escaped = escaped.replace(/&lt;function=[a-zA-Z0-9_]+&gt;[\s\S]*?(?:&lt;\/function&gt;|$)/gi, "");
      escaped = escaped.replace(/&lt;invoke\s+name=[^&gt;]+&gt;[\s\S]*?(?:&lt;\/invoke&gt;|$)/gi, "");
      escaped = escaped.replace(/&lt;parameter=[a-zA-Z0-9_]+&gt;[\s\S]*?(?:&lt;\/parameter&gt;|$)/gi, "");
      escaped = escaped.replace(/&lt;\/?(?:tool_call|function|parameter|invoke|DSML)[^&gt;]*&gt;/gi, "");

      // Replace structured Action Tags: [ACTION:TYPE:VALUE]
      var renderSecretsBtn = function () {
        var sid = window.AiHelper && window.AiHelper.sessionId ? window.AiHelper.sessionId : "";
        return (
          '<button type="button" class="ai-action-tag ai-action-tag--secrets" ' +
          'data-action="UNLOCK_SENSITIVE_FILE" data-session-id="' + sid + '" ' +
          'title="Grant permission to view credential files for this session">' +
          '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:3px;"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg> Unlock Credentials' +
          '</button>'
        );
      };

      // Model-authored unlock tags are ignored. Only the server emits the verified
      // UNLOCK_SENSITIVE_FILE tag after a read tool actually returned secrets_blocked.
      escaped = escaped.replace(/\[ACTION:(?:ALLOW_SECRETS|UNLOCK_CREDENTIALS):?[^\]]*\]/gi, "");

      escaped = escaped.replace(
        /\[ACTION:([A-Z_]+)(?::([^\]]+))?\]/g,
        function (_, actionType, rawActionVal) {
          var actionVal = (rawActionVal || "").trim();
          if (actionType === "UNLOCK_SENSITIVE_FILE") {
            return renderSecretsBtn();
          }
          if (actionType === "OPEN_DEPENDENCY" && /^[a-z0-9_-]{1,64}$/i.test(actionVal)) {
            var dependencyId = actionVal;
            return '<a class="ai-action-tag" href="/dependencies/' + encodeURIComponent(dependencyId) + '">Open Dependencies</a>';
          }
          // Setup Lifecycle & Recovery Action Buttons
          if (actionType === "SETUP_RETRY_INSPECTION") {
            var targetAttr = actionVal ? ' data-target="' + actionVal.replace(/"/g, '&quot;') + '"' : '';
            return (
              '<button type="button" class="btn btn--secondary btn--sm ai-action-btn" data-action="SETUP_RETRY_INSPECTION"' + targetAttr + ' style="margin: 4px 4px 4px 0; font-size: 11px; padding: 4px 10px; display: inline-flex; align-items: center; gap: 4px;">' +
              '  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg> Retry Inspection' +
              '</button>'
            );
          }
          if (actionType === "SETUP_EDIT_ANSWERS") {
            return (
              '<button type="button" class="btn btn--secondary btn--sm ai-action-btn" data-action="SETUP_EDIT_ANSWERS" style="margin: 4px 4px 4px 0; font-size: 11px; padding: 4px 10px; display: inline-flex; align-items: center; gap: 4px;">' +
              '  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg> Edit Answers' +
              '</button>'
            );
          }
          if (actionType === "SETUP_RETRY_PLAN") {
            return (
              '<button type="button" class="btn btn--primary btn--sm ai-action-btn" data-action="SETUP_RETRY_PLAN" style="margin: 4px 4px 4px 0; font-size: 11px; padding: 4px 10px; display: inline-flex; align-items: center; gap: 4px;">' +
              '  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg> Retry Setup Plan' +
              '</button>'
            );
          }
          if (actionType === "SETUP_CHANGE_SOURCE" || actionType === "SETUP_CHANGE_METHOD") {
            var btnLabel = actionType === "SETUP_CHANGE_METHOD" ? "Change Method" : "Change Source";
            return (
              '<button type="button" class="btn btn--secondary btn--sm ai-action-btn" data-action="' + actionType + '" style="margin: 4px 4px 4px 0; font-size: 11px; padding: 4px 10px; display: inline-flex; align-items: center; gap: 4px;">' +
              '  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5"></path><path d="M4 20L21 3"></path><path d="M21 16v5h-5"></path><path d="M15 15l6 6"></path><path d="M4 4l5 5"></path></svg> ' + btnLabel +
              '</button>'
            );
          }
          // Special: SECURITY_FINDING renders as coloured severity badge
          if (actionType === "SECURITY_FINDING") {
            var parts = actionVal.match(/^(critical|warning|ok):(.+)$/i);
            if (parts) {
              var sev = parts[1].toLowerCase();
              var desc = parts[2];
              var cls = sev === "critical" ? "ai-security-badge--critical" : sev === "warning" ? "ai-security-badge--warning" : "ai-security-badge--ok";
              return '<span class="ai-security-badge ' + cls + '"><span class="ai-sec-dot ai-sec-dot--' + sev + '"></span> ' + desc + '</span>';
            }
          }
          // Only server-appended setup handoffs render in chat. The click applies to wizard for review.
          if (actionType === "APP_SETUP_PLAN") {
            var planParts = actionVal.split(":");
            var setupPlanId = planParts[0].trim();
            var planKind = (planParts[1] || "").trim().toLowerCase();
            var isPatch = planKind === "patch" || planKind === "redeploy" || planKind === "fix";
            var cardTitle = isPatch ? "Fix Ready" : "Setup Plan Ready";
            var summaryText = isPatch
              ? "Configuration and required secrets are verified. Click Apply Plan to review settings on the App page before redeploying."
              : "Configuration and required secrets are verified. Click Apply Plan to review settings in the App Engine wizard before deploying.";
            var btnText = isPatch ? "Apply Fix & Review" : "Apply Plan";
            return [
              '<div class="ai-app-plan-card" data-plan-id="' + setupPlanId + '">',
              '  <div class="ai-app-plan-card-header">',
              '    <div class="ai-app-plan-card-header-left">',
              '      <span class="ai-app-plan-card-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg></span>',
              '      <span class="ai-app-plan-card-title">' + cardTitle + '</span>',
              '    </div>',
              '    <span class="badge badge--ok" style="font-size: 10px; font-weight: 600;">Verified</span>',
              '  </div>',
              '  <div class="ai-app-plan-card-body">',
              '    <p class="ai-app-plan-summary">' + summaryText + '</p>',
              '    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">',
              '      <button type="button" class="ai-action-btn--big-next" data-action="APP_SETUP_PLAN" data-plan-id="' + setupPlanId + '">',
              '        <span class="ai-btn-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></span>',
              '        <span class="ai-btn-text">' + btnText + '</span>',
              '        <span class="ai-btn-arrow">→</span>',
              '      </button>',
              '    </div>',
              '  </div>',
              '</div>',
            ].join("\n");
          }
          // App Engine deployment controls never render inside chat.
          if (["APP_PLAN", "APP_DEPLOY", "APP_REDEPLOY", "APP_REBUILD"].indexOf(actionType) !== -1) {
            return '<p class="text-muted">Review App Engine deployment changes on the App page.</p>';
          }
          // Special: APP_NEXT renders a prominent next step button
          if (actionType === "APP_NEXT" || actionType === "APP_STEP") {
            var btnText = actionVal || "Accept & Continue";
            return (
              '<div style="margin: 10px 0;">' +
              '  <button type="button" class="ai-action-btn--big-next" data-action="APP_NEXT">' +
              '    <span class="ai-btn-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></span>' +
              '    <span class="ai-btn-text">' + btnText + '</span>' +
              '    <span class="ai-btn-arrow">→</span>' +
              '  </button>' +
              '</div>'
            );
          }
          // Options are rendered in the pinned decision bar above input; strip from message bubble
          if (actionType === "OPTION" || actionType === "QUICK_REPLY" || actionType === "CHOICE") {
            return "";
          }
          var label = actionType.replace(/_/g, " ").toLowerCase();
          return (
            '<span class="ai-action-tag" data-action="' +
            actionType +
            '" data-copy="' +
            actionVal +
            '" title="Click to apply/copy ' +
            label +
            '">' +
            actionVal +
            "</span>"
          );
        }
      );

      // Strip leading list bullets (- or * or •) and standalone [OPTION:...] / [INPUT:...] from message body
      escaped = escaped.replace(/(?:^[ \t]*[-*•][ \t]*|(?:\r?\n)[ \t]*[-*•][ \t]*)?\[(?:OPTION|INPUT):[^\]]+\]/gi, "");


      // Code blocks ```lang ... ``` — branched rendering by language type
      escaped = escaped.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, function (_, lang, code) {
        var cleanLang = (lang || "code").toLowerCase();
        var trimmedCode = code.replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/^\n+|\n+$/g, "");
        var codeLines = trimmedCode.split("\n");
        var lineCount = codeLines.length;
        var encodedCode = encodeURIComponent(trimmedCode);

        // --- Security findings card ---
        if (cleanLang === "security") {
          var secHtml = codeLines.map(function (line) {
            var t = line.trim();
            if (!t) return "";
            var m = t.match(/^\[(CRITICAL|WARNING|OK|INFO)\]\s*(.+)$/i);
            if (m) {
              var sev = m[1].toUpperCase();
              var cls = sev === "CRITICAL" ? "ai-sec-critical" : sev === "WARNING" ? "ai-sec-warning" : sev === "OK" ? "ai-sec-ok" : "ai-sec-info";
              var sevKey = sev.toLowerCase();
              return '<div class="ai-sec-row ' + cls + '"><span class="ai-sec-dot ai-sec-dot--' + sevKey + '"></span><span class="ai-sec-text">' + m[2] + '</span></div>';
            }
            return '<div class="ai-sec-row ai-sec-info"><span class="ai-sec-dot ai-sec-dot--info"></span><span class="ai-sec-text">' + t + '</span></div>';
          }).join("");
          return [
            '<div class="ai-security-card">',
            '  <div class="ai-security-card-header">',
            '    <div class="ai-security-card-header-left">',
            '      <span class="ai-security-card-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg></span>',
            '      <span class="ai-security-card-title">Security Audit</span>',
            '    </div>',
            '    <button type="button" class="ai-card-strip-btn ai-card-strip-copy-btn" title="Copy findings"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy</button>',
            '  </div>',
            '  <div class="ai-security-card-body">' + secHtml + '</div>',
            '</div>',
          ].join("\n");
        }

        // --- Log output card ---
        if (cleanLang === "log" || cleanLang === "text" || cleanLang === "plaintext") {
          return [
            '<div class="ai-card-strip ai-card-strip--log" data-lang="log" data-code="' + encodedCode + '" data-title="log-output.txt">',
            '  <div class="ai-card-strip-left">',
            '    <span class="ai-card-strip-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><line x1="10" y1="9" x2="8" y2="9"></line></svg></span>',
            '    <span class="ai-card-strip-lang">LOG</span>',
            '    <span class="ai-card-strip-name">Output</span>',
            '    <span class="ai-card-strip-count">' + lineCount + (lineCount === 1 ? " line" : " lines") + '</span>',
            '  </div>',
            '  <div class="ai-card-strip-actions">',
            '    <button type="button" class="ai-card-strip-btn ai-card-strip-expand-btn" data-ai-code-view="true" title="Open log in viewer"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"></polyline><polyline points="9 21 3 21 3 15"></polyline><line x1="21" y1="3" x2="14" y2="10"></line><line x1="3" y1="21" x2="10" y2="14"></line></svg> Expand</button>',
            '    <button type="button" class="ai-card-strip-btn ai-card-strip-copy-btn" title="Copy log"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg></button>',
            '  </div>',
            '</div>',
          ].join("\n");
        }

        // --- JSON data card ---
        if (cleanLang === "json") {
          return [
            '<div class="ai-card-strip ai-card-strip--json" data-lang="json" data-code="' + encodedCode + '" data-title="data.json">',
            '  <div class="ai-card-strip-left">',
            '    <span class="ai-card-strip-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg></span>',
            '    <span class="ai-card-strip-lang">JSON</span>',
            '    <span class="ai-card-strip-name">Data</span>',
            '    <span class="ai-card-strip-count">' + lineCount + (lineCount === 1 ? " line" : " lines") + '</span>',
            '  </div>',
            '  <div class="ai-card-strip-actions">',
            '    <button type="button" class="ai-card-strip-btn ai-card-strip-expand-btn" data-ai-code-view="true" title="Open JSON in viewer"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"></polyline><polyline points="9 21 3 21 3 15"></polyline><line x1="21" y1="3" x2="14" y2="10"></line><line x1="3" y1="21" x2="10" y2="14"></line></svg> Expand</button>',
            '    <button type="button" class="ai-card-strip-btn ai-card-strip-copy-btn" title="Copy JSON"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg></button>',
            '  </div>',
            '</div>',
          ].join("\n");
        }

        // --- Directory / Files card in code block ---
        if (cleanLang === "files" || cleanLang === "dir" || cleanLang === "tree" || cleanLang === "directory") {
          return self._buildDirectoryCard(codeLines);
        }
        if ((cleanLang === "markdown" || cleanLang === "text" || cleanLang === "plaintext" || cleanLang === "code" || !cleanLang) &&
            codeLines.length >= 2 &&
            codeLines.filter(function(l) { return l.indexOf("📁") !== -1 || l.indexOf("📄") !== -1; }).length >= Math.min(2, codeLines.length)) {
          return self._buildDirectoryCard(codeLines);
        }

        // --- Default: code card (existing behaviour) ---
        var filename = EXT_MAP[cleanLang] || ("snippet." + cleanLang);
        return [
          '<div class="ai-card-strip" data-lang="' + cleanLang + '" data-code="' + encodedCode + '" data-title="' + filename + '">',
          '  <div class="ai-card-strip-left">',
          '    <span class="ai-card-strip-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg></span>',
          '    <span class="ai-card-strip-lang">' + cleanLang.toUpperCase() + '</span>',
          '    <span class="ai-card-strip-name">' + filename + '</span>',
          '    <span class="ai-card-strip-count">' + lineCount + (lineCount === 1 ? " line" : " lines") + '</span>',
          '  </div>',
          '  <div class="ai-card-strip-actions">',
          '    <button type="button" class="ai-card-strip-btn ai-card-strip-expand-btn" data-ai-code-view="true" title="Open full code in Split Viewer"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"></polyline><polyline points="9 21 3 21 3 15"></polyline><line x1="21" y1="3" x2="14" y2="10"></line><line x1="3" y1="21" x2="10" y2="14"></line></svg> Expand</button>',
          '    <button type="button" class="ai-card-strip-btn ai-card-strip-copy-btn" title="Copy code"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg></button>',
          '  </div>',
          '</div>',
        ].join("\n");
      });

      // Headings: #### H4, ### H3, ## H2, # H1
      escaped = escaped.replace(/(?:^|\n)####\s+([^\n]+)/g, '\n<h5 class="ai-msg-h5">$1</h5>');
      escaped = escaped.replace(/(?:^|\n)###\s+([^\n]+)/g, '\n<h4 class="ai-msg-h4">$1</h4>');
      escaped = escaped.replace(/(?:^|\n)##\s+([^\n]+)/g, '\n<h3 class="ai-msg-h3">$1</h3>');
      escaped = escaped.replace(/(?:^|\n)#\s+([^\n]+)/g, '\n<h2 class="ai-msg-h2">$1</h2>');

      // Inline code `code`
      escaped = escaped.replace(/`([^`]+)`/g, "<code>$1</code>");

      // Bold **text** or __text__
      escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

      // Italic *text* or _text_
      escaped = escaped.replace(/\*([^*]+)\*/g, "<em>$1</em>");

      // Markdown Tables (with or without outer pipes)
      escaped = escaped.replace(/(?:^|\n)((?:\|?[^\n|]+\|[^\n]+\n)(?:\|?[-:| ]+[-:| ]*\|[-:| ]*\n)(?:(?:\|?[^\n|]+\|[^\n]+(?:\n|$))+))/g, function (match) {
        var lines = match.trim().split("\n");
        if (lines.length < 3) return match;

        var parseRow = function (rowLine) {
          var trimmed = rowLine.trim();
          if (trimmed.startsWith("|")) trimmed = trimmed.substring(1);
          if (trimmed.endsWith("|")) trimmed = trimmed.substring(0, trimmed.length - 1);
          return trimmed.split("|").map(function (c) { return c.trim(); });
        };

        var headerCols = parseRow(lines[0]);
        var ths = headerCols.map(function (c) { return "<th>" + c + "</th>"; }).join("");

        // Check if 2-column key-value / record table (e.g. Field | Value, Key | Value)
        var isKeyValue = headerCols.length === 2;
        var tableCls = isKeyValue ? "ai-table ai-table--keyvalue" : "ai-table";

        var trs = [];
        for (var i = 2; i < lines.length; i++) {
          if (!lines[i].trim()) continue;
          var rowCols = parseRow(lines[i]);
          var tds = rowCols.map(function (c, colIdx) {
            var colCls = (isKeyValue && colIdx === 0) ? ' class="ai-col-key"' : ((isKeyValue && colIdx === 1) ? ' class="ai-col-val"' : '');
            return "<td" + colCls + ">" + c + "</td>";
          }).join("");
          trs.push("<tr>" + tds + "</tr>");
        }

        return (
          '\n<div class="ai-table-wrap"><table class="' + tableCls + '"><thead><tr>' +
          ths +
          "</tr></thead><tbody>" +
          trs.join("") +
          "</tbody></table></div>\n"
        );
      });

      // Checklist items: - [x] task / - [ ] task
      escaped = escaped.replace(
        /(?:^|\n)- \[(x| )\] (.*)/gi,
        function (_, checked, taskText) {
          var isChecked = checked.toLowerCase() === "x";
          var checkCls = isChecked ? "ai-checklist-item ai-checklist-item--checked" : "ai-checklist-item";
          var icon = isChecked ? "✓" : "○";
          return '\n<div class="' + checkCls + '"><span class="ai-check-icon">' + icon + "</span><span>" + taskText + "</span></div>";
        }
      );

      // Directory / File List -> Beautiful Inline File Tree Explorer Card
      escaped = escaped.replace(/(?:^|\n)((?:(?:- |\* |)\s*(?:📁|📄|\[DIR\]|\[FILE:?\])[^\n]+(?:\n|$))+)/gi, function (match) {
        var lines = match.trim().split("\n");
        return "\n" + self._buildDirectoryCard(lines) + "\n";
      });

      // Unordered lists: lines starting with `- ` or `* `
      escaped = escaped.replace(/(?:^|\n)((?:(?:- |\* )[^\n]+(?:\n|$))+)/g, function (match) {
        if (match.indexOf('class="ai-') !== -1 || match.indexOf('ai-file-') !== -1) return match;
        var lines = match.trim().split("\n");
        var lis = lines.map(function (l) {
          var itemText = l.replace(/^[-*]\s+/, "").trim();
          return "<li>" + itemText + "</li>";
        }).join("");
        return '\n<ul class="ai-msg-ul">' + lis + '</ul>\n';
      });

      // Blockquotes > quote
      escaped = escaped.replace(/(?:^|\n)&gt; (.*)/g, '\n<blockquote class="ai-blockquote">$1</blockquote>');

      // Convert double newlines to paragraphs
      var paragraphs = escaped.split(/\n\n+/);
      return paragraphs
        .map(function (p) {
          var trimmed = p.trim();
          if (!trimmed) return "";
          if (
            trimmed.startsWith("<div class=\"ai-card-strip\"") ||
            trimmed.startsWith("<div class=\"ai-file-tree-card\"") ||
            trimmed.startsWith("<div class=\"ai-security-card\"") ||
            trimmed.startsWith("<div class=\"ai-table-wrap\"") ||
            trimmed.startsWith("<div class=\"ai-thought-box\"") ||
            trimmed.startsWith("<blockquote class=\"ai-blockquote\"") ||
            trimmed.startsWith("<div class=\"ai-checklist-item\"") ||
            trimmed.startsWith("<ul class=\"ai-msg-ul\"") ||
            trimmed.startsWith("<ol class=\"ai-msg-ol\"") ||
            trimmed.startsWith("<h2") ||
            trimmed.startsWith("<h3") ||
            trimmed.startsWith("<h4") ||
            trimmed.startsWith("<h5")
          ) {
            return trimmed;
          }
          return "<p>" + trimmed.replace(/\n/g, "<br>") + "</p>";
        })
        .join("");
    },
  };

  window.AiHelperMarkdown = AiHelperMarkdown;
})();
