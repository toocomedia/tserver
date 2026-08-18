/**
 * chat_markdown.js — Markdown & Action Tag Parser for AI Assistant messages.
 */
(function () {
  "use strict";

  var AiHelperMarkdown = {
    render: function (text) {
      if (!text) return "";
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
            '" title="Click to copy ' +
            label +
            '">' +
            actionVal +
            "</span>"
          );
        }
      );

      // Code blocks ```lang ... ```
      escaped = escaped.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, function (_, lang, code) {
        return (
          '<div class="ai-code-block">' +
          '<button type="button" class="ai-code-copy-btn">Copy</button>' +
          "<pre><code>" + code.trim() + "</code></pre>" +
          "</div>"
        );
      });

      // Inline code `code`
      escaped = escaped.replace(/`([^`]+)`/g, "<code>$1</code>");

      // Bold **text**
      escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

      // Italic *text*
      escaped = escaped.replace(/\*([^*]+)\*/g, "<em>$1</em>");

      // Convert newlines to paragraphs/breaks
      var paragraphs = escaped.split(/\n\n+/);
      return paragraphs
        .map(function (p) {
          return "<p>" + p.replace(/\n/g, "<br>") + "</p>";
        })
        .join("");
    },
  };

  window.AiHelperMarkdown = AiHelperMarkdown;
})();
