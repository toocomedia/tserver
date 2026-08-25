/**
 * chat_decision_bar.js — Floating Quick-Decision Bar pinned above the Chat Input Box.
 * Allows 1-click interactive selection when the AI proposes multiple setup methods.
 */
(function () {
  "use strict";

  var AiHelperDecisionBar = {
    containerEl: null,
    onSelectCallback: null,

    init: function (containerEl, onSelectCallback) {
      this.containerEl = containerEl;
      this.onSelectCallback = onSelectCallback;
    },

    show: function (options, promptTitle) {
      if (!this.containerEl || !options || !options.length) return;
      var self = this;
      var title = promptTitle || "Select Setup Method:";

      var html = [
        '<div class="ai-decision-bar-inner">',
        '  <div class="ai-decision-bar-header">',
        '    <div class="ai-decision-bar-header-left">',
        '      <span class="ai-decision-bar-title">' + this._escapeHtml(title) + "</span>",
        "    </div>",
        '    <button type="button" class="ai-decision-bar-close" title="Dismiss">✕</button>',
        "  </div>",
        '  <div class="ai-decision-bar-options">',
      ];

      options.forEach(function (opt, idx) {
        var isRecommended = opt.isRecommended || /recommended/i.test(opt.label);
        var badgeHtml = isRecommended
          ? '<span class="ai-decision-badge ai-decision-badge--rec">Recommended</span>'
          : '<span class="ai-decision-badge">Option ' + (idx + 1) + "</span>";

        html.push(
          '    <button type="button" class="ai-decision-btn" data-reply="' + self._escapeHtml(opt.reply) + '">',
          '      <div class="ai-decision-btn-top">',
          '        <span class="ai-decision-btn-label">' + self._escapeHtml(opt.label) + "</span>",
          "        " + badgeHtml,
          "      </div>",
          opt.description ? '      <div class="ai-decision-btn-desc">' + self._escapeHtml(opt.description) + "</div>" : "",
          "    </button>"
        );
      });

      html.push("  </div>", "</div>");
      this.containerEl.innerHTML = html.join("\n");
      this.containerEl.style.display = "block";

      // Attach click handlers
      var closeBtn = this.containerEl.querySelector(".ai-decision-bar-close");
      if (closeBtn) {
        closeBtn.addEventListener("click", function () {
          self.hide();
        });
      }

      var btns = this.containerEl.querySelectorAll(".ai-decision-btn");
      btns.forEach(function (btn) {
        btn.addEventListener("click", function () {
          var reply = btn.getAttribute("data-reply");
          self.hide();
          if (typeof self.onSelectCallback === "function") {
            self.onSelectCallback(reply);
          }
        });
      });
    },

    hide: function () {
      if (this.containerEl) {
        this.containerEl.style.display = "none";
        this.containerEl.innerHTML = "";
      }
    },

    extractAndShow: function (text) {
      if (!text || typeof text !== "string") return;
      var options = [];
      var regex = /\[OPTION:([^\]]+)\]/gi;
      var match;

      while ((match = regex.exec(text)) !== null) {
        var parts = match[1].split("|");
        var optLabel = parts[0].trim();
        var optReply = (parts[1] || parts[0]).trim();
        options.push({
          label: optLabel,
          reply: optReply,
          isRecommended: /recommended/i.test(optLabel),
        });
      }

      // Fallback: If AI formatted as Option 1... Option 2... without brackets
      if (options.length === 0 && /Option 1[\s\S]*Option 2/i.test(text)) {
        var opt1Match = text.match(/Option 1[^\n:]*:\s*([^\n]+)/i);
        var opt2Match = text.match(/Option 2[^\n:]*:\s*([^\n]+)/i);
        if (opt1Match && opt2Match) {
          options.push({
            label: "Option 1 (Recommended): " + opt1Match[1].trim(),
            reply: "Option 1",
            isRecommended: true,
          });
          options.push({
            label: "Option 2: " + opt2Match[1].trim(),
            reply: "Option 2",
            isRecommended: false,
          });
        }
      }

      if (options.length > 0) {
        this.show(options, "Select Setup Method:");
      }
    },

    _escapeHtml: function (str) {
      return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    },
  };

  window.AiHelperDecisionBar = AiHelperDecisionBar;
})();
