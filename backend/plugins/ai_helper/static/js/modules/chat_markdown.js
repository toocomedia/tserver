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
          .replace(/[📁📄🗂️📂]/g, "")
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
          // File extension icon helper
          var extMatch = clean.match(/\.([a-zA-Z0-9_-]+)$/);
          var ext = extMatch ? extMatch[1].toLowerCase() : "";
          var fileType = "file";
          var icon = "📄";
          if (ext === "py") { icon = "🐍"; fileType = "python"; }
          else if (ext === "js" || ext === "ts" || ext === "jsx" || ext === "tsx") { icon = "🟨"; fileType = "js"; }
          else if (ext === "json" || ext === "yaml" || ext === "yml" || ext === "ini" || ext === "toml") { icon = "⚙️"; fileType = "config"; }
          else if (clean.startsWith(".env") || ext === "env") { icon = "🔐"; fileType = "env"; }
          else if (ext === "sh" || ext === "bash") { icon = "💻"; fileType = "shell"; }
          else if (ext === "md" || ext === "txt" || ext === "rst") { icon = "📝"; fileType = "doc"; }
          else if (ext === "html" || ext === "css" || ext === "scss") { icon = "🌐"; fileType = "web"; }
          else if (ext === "sql" || ext === "db") { icon = "🗄️"; fileType = "db"; }

          files.push({ name: clean, meta: meta, icon: icon, fileType: fileType });
        }
      });

      if (folders.length === 0 && files.length === 0) return "";

      var totalCount = folders.length + files.length;
      var countLabel = "";
      if (folders.length > 0 && files.length > 0) {
        countLabel = folders.length + " dir" + (folders.length > 1 ? "s" : "") + ", " + files.length + " file" + (files.length > 1 ? "s" : "");
      } else if (folders.length > 0) {
        countLabel = folders.length + " folder" + (folders.length > 1 ? "s" : "");
      } else {
        countLabel = files.length + " file" + (files.length > 1 ? "s" : "");
      }

      var encodedNames = encodeURIComponent(plainNames.join("\n"));

      var foldersHtml = "";
      if (folders.length > 0) {
        foldersHtml = folders.map(function (f) {
          return (
            '<div class="ai-file-item ai-file-item--folder" data-name="' + f.name + '" title="' + f.name + '">' +
            '  <span class="ai-file-icon">📁</span>' +
            '  <span class="ai-file-name">' + f.name + '</span>' +
            (f.meta ? '  <span class="ai-file-meta">' + f.meta + '</span>' : '') +
            '</div>'
          );
        }).join("");
      }

      var filesHtml = "";
      if (files.length > 0) {
        filesHtml = files.map(function (f) {
          var metaBadge = "";
          if (f.meta) {
            var cleanMeta = f.meta.replace(/,\s*sensitive\s*—\s*/gi, " · ").replace(/,\s*/g, " · ");
            var isMasked = f.meta.toLowerCase().indexOf("mask") !== -1;
            metaBadge = '<span class="ai-file-meta' + (isMasked ? ' ai-file-meta--masked' : '') + '">' + cleanMeta + '</span>';
          }
          return (
            '<div class="ai-file-item ai-file-item--file ai-file-item--' + f.fileType + '" data-name="' + f.name + '" title="' + f.name + '">' +
            '  <span class="ai-file-icon">' + f.icon + '</span>' +
            '  <span class="ai-file-name">' + f.name + '</span>' +
            metaBadge +
            '</div>'
          );
        }).join("");
      }

      return [
        '<div class="ai-file-tree-card" data-raw-names="' + encodedNames + '">',
        '  <div class="ai-file-tree-header">',
        '    <div class="ai-file-tree-header-left">',
        '      <span class="ai-file-tree-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg></span>',
        '      <span class="ai-file-tree-title">Directory Contents</span>',
        '      <span class="ai-file-tree-count">' + countLabel + '</span>',
        '    </div>',
        '    <div class="ai-file-tree-actions">',
        '      <button type="button" class="ai-file-tree-copy-btn" title="Copy file names"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy</button>',
        '    </div>',
        '  </div>',
        '  <div class="ai-file-tree-body">',
        '    <div class="ai-file-tree-grid">' + foldersHtml + filesHtml + '</div>',
        '  </div>',
        '</div>',
      ].join("\n");
    },

    render: function (text) {
      if (!text) return "";

      var thoughtHtml = "";
      var mainText = text;

      // 1. Extract and render <think>...</think> anywhere in the message
      var thinkMatchClosed = mainText.match(/<think>([\s\S]*?)<\/think>/i);
      if (thinkMatchClosed) {
        var thoughtContent = this._renderMarkdownCore(thinkMatchClosed[1].trim());
        thoughtHtml = [
          '<div class="ai-thought-box" data-state="collapsed">',
          '  <div class="ai-thought-header">',
          '    <div class="ai-thought-header-left">',
          '      <span class="ai-thought-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a7 7 0 0 0-7 7c0 2.38 1.19 4.47 3 5.74V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.26c1.81-1.27 3-3.36 3-5.74a7 7 0 0 0-7-7z"></path><path d="M9 21h6"></path></svg></span>',
          '      <span class="ai-thought-title">Thought Process</span>',
          "    </div>",
          '    <span class="ai-thought-chevron">▾</span>',
          "  </div>",
          '  <div class="ai-thought-body">' + (thoughtContent || "<em>No reasoning logs</em>") + "</div>",
          "</div>",
        ].join("\n");
        mainText = mainText.replace(/<think>[\s\S]*?<\/think>/i, "").trim();
      } else {
        // Active streaming unclosed <think>...
        var thinkMatchUnclosed = mainText.match(/<think>([\s\S]*)$/i);
        if (thinkMatchUnclosed) {
          var rawThought = thinkMatchUnclosed[1].trim();
          var liveThoughtContent = this._renderMarkdownCore(rawThought);
          thoughtHtml = [
            '<div class="ai-thought-box ai-thought-box--thinking" data-state="expanded">',
            '  <div class="ai-thought-header">',
            '    <div class="ai-thought-header-left">',
            '      <span class="ai-thought-icon ai-thought-pulse"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg></span>',
            '      <span class="ai-thought-title">Thinking...</span>',
            '      <span class="ai-thought-live-badge">Live</span>',
            "    </div>",
            '    <span class="ai-thought-chevron">▴</span>',
            "  </div>",
            '  <div class="ai-thought-body">' + (liveThoughtContent || "<em>Analyzing request...</em>") + "</div>",
            "</div>",
          ].join("\n");
          mainText = mainText.substring(0, thinkMatchUnclosed.index).trim();
        } else {
          // Auto-capture untagged chain-of-thought monologue
          var metaReasoningMatch = mainText.match(/^(?:The user wants me to|Now I have the information for|I called the tool|The tool suggests using|Let me structure the response)[\s\S]*?(?=\n\n(?:Here['’]s|📁|📄|```|\*\*|#|[A-Z][a-z]+ is |To |You can|$))/i);
          if (metaReasoningMatch && metaReasoningMatch[0].length < mainText.length) {
            var reasoningText = metaReasoningMatch[0].trim();
            var thoughtBody = this._renderMarkdownCore(reasoningText);
            thoughtHtml = [
              '<div class="ai-thought-box" data-state="collapsed">',
              '  <div class="ai-thought-header">',
              '    <div class="ai-thought-header-left">',
              '      <span class="ai-thought-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a7 7 0 0 0-7 7c0 2.38 1.19 4.47 3 5.74V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.26c1.81-1.27 3-3.36 3-5.74a7 7 0 0 0-7-7z"></path><path d="M9 21h6"></path></svg></span>',
              '      <span class="ai-thought-title">Thought Process</span>',
              "    </div>",
              '    <span class="ai-thought-chevron">▾</span>',
              "  </div>",
              '  <div class="ai-thought-body">' + (thoughtBody || "<em>Reasoning logs</em>") + "</div>",
              "</div>",
            ].join("\n");
            mainText = mainText.substring(metaReasoningMatch[0].length).trim();
          }
        }
      }

      var renderedMain = this._renderMarkdownCore(mainText);
      return thoughtHtml + renderedMain;
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
      escaped = escaped.replace(
        /\[ACTION:([A-Z_]+):([^\]]+)\]/g,
        function (_, actionType, actionVal) {
          // Special: ALLOW_SECRETS renders as an amber unlock button
          if (actionType === "ALLOW_SECRETS") {
            var sid = window.AiHelper && window.AiHelper.sessionId ? window.AiHelper.sessionId : "";
            return (
              '<button type="button" class="ai-action-tag ai-action-tag--secrets" ' +
              'data-action="ALLOW_SECRETS" data-session-id="' + sid + '" ' +
              'title="Grant permission to view credential files for this session">' +
              '\uD83D\uDD10 Unlock Credentials' +
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
              var icon = sev === "critical" ? "\u274C" : sev === "warning" ? "\u26A0\uFE0F" : "\u2705";
              return '<span class="ai-security-badge ' + cls + '">' + icon + ' ' + desc + '</span>';
            }
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
              var icon = sev === "CRITICAL" ? "\u274C" : sev === "WARNING" ? "\u26A0\uFE0F" : sev === "OK" ? "\u2705" : "\u2139\uFE0F";
              return '<div class="ai-sec-row ' + cls + '"><span class="ai-sec-icon">' + icon + '</span><span class="ai-sec-text">' + m[2] + '</span></div>';
            }
            return '<div class="ai-sec-row ai-sec-info"><span class="ai-sec-text">' + t + '</span></div>';
          }).join("");
          return [
            '<div class="ai-security-card">',
            '  <div class="ai-security-card-header">',
            '    <span class="ai-security-card-icon">\uD83D\uDD12</span>',
            '    <span class="ai-security-card-title">Security Audit</span>',
            '    <button type="button" class="ai-card-strip-btn ai-card-strip-copy-btn" title="Copy findings"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg></button>',
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

      // Inline code `code`
      escaped = escaped.replace(/`([^`]+)`/g, "<code>$1</code>");

      // Bold **text** or __text__
      escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

      // Italic *text* or _text_
      escaped = escaped.replace(/\*([^*]+)\*/g, "<em>$1</em>");

      // Markdown Tables (| Header | Header | ... |)
      escaped = escaped.replace(/(?:^|\n)(\|.+?\|\n\|[-:| ]+?\|\n(?:\|.+?\|\n?)+)/g, function (match) {
        var lines = match.trim().split("\n");
        if (lines.length < 3) return match;
        var headerCols = lines[0].split("|").slice(1, -1);
        var ths = headerCols.map(function (c) { return "<th>" + c.trim() + "</th>"; }).join("");
        var trs = [];
        for (var i = 2; i < lines.length; i++) {
          var rowCols = lines[i].split("|").slice(1, -1);
          var tds = rowCols.map(function (c) { return "<td>" + c.trim() + "</td>"; }).join("");
          trs.push("<tr>" + tds + "</tr>");
        }
        return (
          '<div class="ai-table-wrap"><table class="ai-table"><thead><tr>' +
          ths +
          "</tr></thead><tbody>" +
          trs.join("") +
          "</tbody></table></div>"
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
            trimmed.startsWith("<div class=\"ai-checklist-item\"")
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
