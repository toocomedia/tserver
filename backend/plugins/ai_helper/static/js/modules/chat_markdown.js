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

      // Code blocks ```lang ... ``` -> One-Line Card Strip with Split Viewer Expand
      escaped = escaped.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, function (_, lang, code) {
        var cleanLang = (lang || "code").toLowerCase();
        var trimmedCode = code.replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/^\n+|\n+$/g, "");
        var codeLines = trimmedCode.split("\n");
        var lineCount = codeLines.length;
        var filename = EXT_MAP[cleanLang] || ("snippet." + cleanLang);
        var encodedCode = encodeURIComponent(trimmedCode);

        return [
          '<div class="ai-card-strip" data-lang="' + cleanLang + '" data-code="' + encodedCode + '" data-title="' + filename + '">',
          '  <div class="ai-card-strip-left">',
          '    <span class="ai-card-strip-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg></span>',
          '    <span class="ai-card-strip-lang">' + cleanLang.toUpperCase() + "</span>",
          '    <span class="ai-card-strip-name">' + filename + "</span>",
          '    <span class="ai-card-strip-count">' + lineCount + (lineCount === 1 ? " line" : " lines") + "</span>",
          "  </div>",
          '  <div class="ai-card-strip-actions">',
          '    <button type="button" class="ai-card-strip-btn ai-card-strip-expand-btn" data-ai-code-view="true" title="Open full code in Split Viewer"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"></polyline><polyline points="9 21 3 21 3 15"></polyline><line x1="21" y1="3" x2="14" y2="10"></line><line x1="3" y1="21" x2="10" y2="14"></line></svg> Expand</button>',
          '    <button type="button" class="ai-card-strip-btn ai-card-strip-copy-btn" title="Copy code"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg></button>',
          "  </div>",
          "</div>",
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

      // Directory / File List -> One-Line Card Strip with Split Viewer Expand
      escaped = escaped.replace(/(?:^|\n)((?:- (?:📁|📄|\[FILE:)[^\n]+(?:\n|$))+)/gi, function (match) {
        var lines = match.trim().split("\n");
        var formattedLines = lines.map(function (l) { return l.replace(/^- /, "").trim(); }).join("\n");
        var encodedList = encodeURIComponent(formattedLines);

        return [
          '\n<div class="ai-card-strip ai-card-strip--list" data-lang="markdown" data-code="' + encodedList + '" data-title="Directory Contents">',
          '  <div class="ai-card-strip-left">',
          '    <span class="ai-card-strip-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg></span>',
          '    <span class="ai-card-strip-lang">FILES</span>',
          '    <span class="ai-card-strip-name">Directory Contents</span>',
          '    <span class="ai-card-strip-count">' + lines.length + (lines.length === 1 ? " item" : " items") + "</span>",
          "  </div>",
          '  <div class="ai-card-strip-actions">',
          '    <button type="button" class="ai-card-strip-btn ai-card-strip-expand-btn" data-ai-code-view="true" title="Open full list in Split Viewer"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"></polyline><polyline points="9 21 3 21 3 15"></polyline><line x1="21" y1="3" x2="14" y2="10"></line><line x1="3" y1="21" x2="10" y2="14"></line></svg> Expand</button>',
          '    <button type="button" class="ai-card-strip-btn ai-card-strip-copy-btn" title="Copy list"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg></button>',
          "  </div>",
          "</div>",
        ].join("\n");
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
