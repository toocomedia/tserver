/**
 * chat_decision_bar.js — Interactive Setup & Configuration Card pinned above Chat Input.
 * Allows step-by-step option selection and credential input collection before submitting to AI.
 */
(function () {
  "use strict";

  var AiHelperDecisionBar = {
    containerEl: null,
    onSelectCallback: null,
    state: {
      selectedOption: null,
    },

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
      self.state.selectedOption = options.length ? options[0].reply : null;
      var hasBoth = options.length > 0 && inputs.length > 0;
      var title = promptTitle || (inputs.length ? "Configuration & Setup Method:" : "Select Setup Method:");

      var html = [
        '<div class="ai-decision-card" style="background: var(--color-surface, #1e1e1e); border: 1px solid var(--color-line, rgba(255,255,255,0.12)); border-radius: 8px; padding: 12px; margin: 8px 12px 10px 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.25); color: var(--color-text, #ffffff); font-family: inherit;">',
        '  <div class="ai-decision-card-header" style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 10px;">',
        '    <span style="font-size:12px; font-weight:600; color:var(--color-text, #ffffff); display:flex; align-items:center; gap:6px;">' +
        '      <span style="display:inline-block; width:6px; height:6px; border-radius:50%; background:var(--color-accent, #6366f1);"></span>' +
        this._escapeHtml(title) +
        '    </span>',
        '    <button type="button" class="ai-decision-card-close" style="background:transparent; border:none; color:var(--color-muted, #94a3b8); cursor:pointer; font-size:14px; padding:2px 6px; line-height:1;" title="Dismiss">✕</button>',
        '  </div>',
      ];

      // Render Options Group
      if (options.length) {
        html.push('  <div class="ai-decision-options-group" style="display:flex; flex-direction:column; gap:6px; margin-bottom:' + (inputs.length ? '10px' : '0') + ';">');
        options.forEach(function (opt, idx) {
          var isRecommended = opt.isRecommended || /recommended/i.test(opt.label);
          var isDefaultSelected = hasBoth && idx === 0;
          var badgeHtml = isRecommended
            ? '<span class="ai-quick-opt-badge" style="font-size:10px; font-weight:600; padding:2px 6px; border-radius:4px; background:rgba(99,102,241,0.2); color:var(--color-accent, #818cf8); margin-left:auto;">Recommended</span>'
            : '';

          html.push(
            '    <button type="button" class="ai-quick-option-btn ' + (isDefaultSelected ? 'is-selected' : '') + '" data-reply="' + self._escapeHtml(opt.reply) + '" style="display:flex; align-items:center; gap:8px; padding:7px 10px; background:var(--color-bg, #121212); border:1px solid ' + (isDefaultSelected ? 'var(--color-accent, #6366f1)' : 'var(--color-line, rgba(255,255,255,0.1))') + '; border-radius:6px; color:var(--color-text, #ffffff); font-size:12px; font-weight:500; cursor:pointer; text-align:left; transition:all 0.15s ease;">',
            '      <span class="ai-opt-radio" style="width:12px; height:12px; border-radius:50%; border:2px solid ' + (isDefaultSelected ? 'var(--color-accent, #6366f1)' : 'var(--color-muted, #64748b)') + '; display:inline-flex; align-items:center; justify-content:center; flex-shrink:0;">' + (isDefaultSelected ? '<span style="width:4px; height:4px; border-radius:50%; background:var(--color-accent, #6366f1);"></span>' : '') + '</span>',
            '      <span style="flex:1; line-height:1.3;">' + self._escapeHtml(opt.label) + '</span>',
            '      ' + badgeHtml,
            '    </button>'
          );
        });
        html.push('  </div>');
      }

      // Render Inputs Group
      if (inputs.length) {
        html.push('  <div class="ai-decision-inputs-group" style="display:flex; flex-direction:column; gap:8px; margin-top:' + (options.length ? '8px' : '0') + ';">');
        inputs.forEach(function (inp) {
          html.push(
            '    <div style="display:flex; flex-direction:column; gap:4px;">',
            '      <label style="font-size:11.5px; font-weight:500; color:var(--color-muted, #94a3b8);">' + self._escapeHtml(inp.label || "Admin Email:") + '</label>',
            '      <div style="display:flex; gap:6px;">',
            '        <input type="text" class="ai-decision-field-input" data-key="' + self._escapeHtml(inp.key) + '" placeholder="' + self._escapeHtml(inp.placeholder || "admin@example.com") + '" style="flex:1; background:var(--color-bg, #121212); border:1px solid var(--color-line, rgba(255,255,255,0.15)); border-radius:6px; padding:7px 10px; color:var(--color-text, #ffffff); font-size:12px; outline:none; box-sizing:border-box;" />',
            '      </div>',
            '    </div>'
          );
        });
        html.push('  </div>');
      }

      // Render Submit Action Button if inputs exist or hasBoth
      if (hasBoth || inputs.length > 0) {
        html.push(
          '  <div style="display:flex; justify-content:flex-end; margin-top:10px;">',
          '    <button type="button" class="ai-decision-submit-all-btn" style="background:var(--color-accent, #6366f1); color:var(--color-on-accent, #ffffff); border:none; border-radius:6px; padding:7px 16px; font-size:12px; font-weight:600; cursor:pointer; transition:opacity 0.15s ease;">Continue</button>',
          '  </div>'
        );
      }

      html.push("</div>");
      this.containerEl.innerHTML = html.join("\n");
      this.containerEl.style.display = "block";

      // Bind close button
      var closeBtn = this.containerEl.querySelector(".ai-decision-card-close");
      if (closeBtn) {
        closeBtn.addEventListener("click", function () {
          self.hide();
        });
      }

      // Handle Option buttons
      var optBtns = this.containerEl.querySelectorAll(".ai-quick-option-btn");
      optBtns.forEach(function (btn) {
        btn.addEventListener("click", function () {
          var reply = btn.getAttribute("data-reply");
          if (!hasBoth) {
            // If only options exist, 1-click submit immediately
            self.hide();
            if (typeof self.onSelectCallback === "function") {
              self.onSelectCallback(reply);
            }
          } else {
            // If both options and inputs exist, toggle selection state without closing
            self.state.selectedOption = reply;
            optBtns.forEach(function (b) {
              var isSel = b === btn;
              b.classList.toggle("is-selected", isSel);
              b.style.borderColor = isSel ? "var(--color-accent, #6366f1)" : "var(--color-line, rgba(255,255,255,0.1))";
              var radio = b.querySelector(".ai-opt-radio");
              if (radio) {
                radio.style.borderColor = isSel ? "var(--color-accent, #6366f1)" : "var(--color-muted, #64748b)";
                radio.innerHTML = isSel ? '<span style="width:4px; height:4px; border-radius:50%; background:var(--color-accent, #6366f1);"></span>' : '';
              }
            });
          }
        });
      });

      // Handle Submit All Button
      var submitAllBtn = this.containerEl.querySelector(".ai-decision-submit-all-btn");
      var doSubmitAll = function () {
        var inputEls = self.containerEl.querySelectorAll(".ai-decision-field-input");
        var inputVals = [];
        inputEls.forEach(function (inp) {
          var val = inp.value.trim();
          if (val) {
            inputVals.push(val);
          }
        });

        var finalMessage = "";
        if (self.state.selectedOption && inputVals.length) {
          finalMessage = self.state.selectedOption + " with admin email: " + inputVals.join(", ");
        } else if (self.state.selectedOption) {
          finalMessage = self.state.selectedOption;
        } else if (inputVals.length) {
          finalMessage = inputVals.join(", ");
        }

        if (!finalMessage) return;
        self.hide();
        if (typeof self.onSelectCallback === "function") {
          self.onSelectCallback(finalMessage);
        }
      };

      if (submitAllBtn) {
        submitAllBtn.addEventListener("click", doSubmitAll);
      }

      // Allow pressing Enter in input fields
      var inputEls = this.containerEl.querySelectorAll(".ai-decision-field-input");
      inputEls.forEach(function (inp) {
        inp.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            e.preventDefault();
            doSubmitAll();
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

      // If a setup action plan or other action tag is present, the plan is finalized — do not show the decision bar
      if (/\[ACTION:APP_SETUP_PLAN:/i.test(text) || /ready to deploy/i.test(text)) {
        this.hide();
        return;
      }

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

      // Fallback: only if no action tags are present and the AI was prompting the user without brackets
      if (!/\[ACTION:/i.test(text)) {
        if (options.length === 0 && /Option 1[\s\S]*Option 2/i.test(text) && /(?:choose|select|confirm|preferred|selection)/i.test(text)) {
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

        if (inputs.length === 0 && /(?:what|enter|provide|specify|require[sd]?)\s+(?:your\s+)?admin\s+email/i.test(text)) {
          inputs.push({
            key: "admin_email",
            placeholder: "admin@example.com",
            label: "Admin Email:",
          });
        }
      }

      if (options.length > 0 || inputs.length > 0) {
        this.show(options, inputs.length ? "Configuration & Setup Method:" : "Select Setup Method:", inputs);
      } else {
        this.hide();
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
