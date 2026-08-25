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

    show: function (options, promptTitle, inputs) {
      if (!this.containerEl) return;
      options = options || [];
      inputs = inputs || [];
      if (!options.length && !inputs.length) return;

      var self = this;
      var title = promptTitle || (inputs.length ? "Required Setup Input & Method:" : "Select Setup Method:");

      var html = [
        '<div class="ai-decision-bar-inner">',
        '  <div class="ai-decision-bar-header">',
        '    <div class="ai-decision-bar-header-left">',
        '      <span class="ai-decision-bar-title">' + this._escapeHtml(title) + "</span>",
        "    </div>",
        '    <button type="button" class="ai-decision-bar-close" title="Dismiss">✕</button>',
        "  </div>",
      ];

      if (inputs.length) {
        html.push('  <div class="ai-decision-bar-inputs" style="padding: 6px 12px 2px 12px;">');
        inputs.forEach(function (inp) {
          html.push(
            '    <div class="ai-decision-input-row" style="display:flex;gap:8px;align-items:center;margin-bottom:6px;">',
            '      <label style="font-size:12px;font-weight:500;color:#cbd5e1;white-space:nowrap;">' + self._escapeHtml(inp.label || "Admin Email:") + '</label>',
            '      <input type="text" class="ai-decision-field-input" data-key="' + self._escapeHtml(inp.key) + '" placeholder="' + self._escapeHtml(inp.placeholder || "admin@example.com") + '" style="flex:1;background:rgba(0,0,0,0.35);border:1px solid rgba(255,255,255,0.18);border-radius:6px;padding:6px 10px;color:#fff;font-size:12px;outline:none;" />',
            '      <button type="button" class="ai-decision-field-submit" style="background:#3b82f6;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer;font-weight:500;white-space:nowrap;">Submit</button>',
            '    </div>'
          );
        });
        html.push('  </div>');
      }

      if (options.length) {
        html.push('  <div class="ai-decision-bar-options">');
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
        html.push("  </div>");
      }

      html.push("</div>");
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

      var inputRows = this.containerEl.querySelectorAll(".ai-decision-input-row");
      inputRows.forEach(function (row) {
        var inputEl = row.querySelector(".ai-decision-field-input");
        var submitBtn = row.querySelector(".ai-decision-field-submit");
        var handleSubmit = function () {
          var val = (inputEl ? inputEl.value : "").trim();
          if (!val) return;
          self.hide();
          if (typeof self.onSelectCallback === "function") {
            self.onSelectCallback(val);
          }
        };
        if (submitBtn) {
          submitBtn.addEventListener("click", handleSubmit);
        }
        if (inputEl) {
          inputEl.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
              e.preventDefault();
              handleSubmit();
            }
          });
        }
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

      var inputs = [];
      var inputRegex = /\[INPUT:([^\]]+)\]/gi;
      var inMatch;
      while ((inMatch = inputRegex.exec(text)) !== null) {
        var inParts = inMatch[1].split("|");
        inputs.push({
          key: inParts[0].trim(),
          placeholder: (inParts[1] || "admin@example.com").trim(),
          label: (inParts[2] || "Admin Email:").trim(),
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

      // Fallback: If AI explicitly asked for admin email in text without an [INPUT:] tag
      if (inputs.length === 0 && /admin email|superuser email|email address to proceed|provide your admin email/i.test(text)) {
        inputs.push({
          key: "admin_email",
          placeholder: "admin@example.com",
          label: "Admin Email:",
        });
      }

      if (options.length > 0 || inputs.length > 0) {
        this.show(options, inputs.length ? "Configuration Input Required:" : "Select Setup Method:", inputs);
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
