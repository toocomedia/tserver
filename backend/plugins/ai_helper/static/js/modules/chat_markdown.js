/**
 * chat_markdown.js — Markdown & Action Tag Parser for AI Assistant messages.
 */
(function () {
  "use strict";

  var AiHelperMarkdown = {
    render: function (text) {
      if (!text) return "";

      var thoughtHtml = "";
      var mainText = text;

      // 1. Extract and render <think>...</think> or active unclosed <think>...
      var thinkMatchClosed = mainText.match(/^<think>([\s\S]*?)<\/think>\s*/i);
      var thinkMatchUnclosed = null;

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
        mainText = mainText.substring(thinkMatchClosed[0].length);
      } else {
        thinkMatchUnclosed = mainText.match(/^<think>([\s\S]*)$/i);
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
          mainText = "";
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

      // Code blocks ```lang ... ```
      escaped = escaped.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, function (_, lang, code) {
        var cleanLang = (lang || "code").toLowerCase();
        return [
          '<div class="ai-code-block" data-lang="' + cleanLang + '">',
          '  <div class="ai-code-header">',
          '    <span class="ai-code-lang">' + cleanLang + "</span>",
          '    <div class="ai-code-actions">',
          '      <button type="button" class="ai-code-expand-btn" data-ai-code-view="true" title="Open Code View Window">Code View</button>',
          '      <button type="button" class="ai-code-copy-btn" title="Copy snippet">Copy</button>',
          "    </div>",
          "  </div>",
          '  <pre><code class="language-' + cleanLang + '">' + code.trim() + "</code></pre>",
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

      // Blockquotes > quote
      escaped = escaped.replace(/(?:^|\n)&gt; (.*)/g, '\n<blockquote class="ai-blockquote">$1</blockquote>');

      // Convert double newlines to paragraphs
      var paragraphs = escaped.split(/\n\n+/);
      return paragraphs
        .map(function (p) {
          var trimmed = p.trim();
          if (!trimmed) return "";
          if (
            trimmed.startsWith("<div class=\"ai-code-block\"") ||
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
