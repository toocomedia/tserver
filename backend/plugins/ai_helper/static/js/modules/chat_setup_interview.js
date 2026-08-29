/**
 * Staged setup interview: hold verified non-secret answers in the browser and
 * invoke chat exactly once after its final step.
 */
(function () {
  "use strict";

  var SECRET_INPUT = /(?:pass(?:word)?|secret|token|api[_-]?key|private[_-]?key|encryption[_-]?key)/i;
  var interview = {
    containerEl: null,
    onComplete: null,
    steps: [],
    index: 0,
    answers: {},
    completed: false,

    init: function (containerEl, onComplete) {
      this.containerEl = containerEl;
      this.onComplete = onComplete;
    },

    extractAndShow: function (text) {
      if (!text || typeof text !== "string") return;
      if (/\[ACTION:APP_SETUP_PLAN:/i.test(text)) {
        this.hide();
        return;
      }
      var parsed = this._parse(text);
      if (!parsed.steps.length) {
        this.hide();
        return;
      }
      this.steps = parsed.steps;
      this.index = 0;
      this.answers = this.answers || {};
      this.completed = false;
      this._render();
    },

    editAnswers: function () {
      if (this.steps && this.steps.length) {
        this.index = 0;
        this.completed = false;
        this._render();
        return true;
      }
      return false;
    },

    _parse: function (text) {
      var deployment = [];
      var providers = {};
      var inputs = [];
      var match;
      var optionRe = /\[OPTION:([^\]]+)\]/gi;
      while ((match = optionRe.exec(text)) !== null) {
        var pieces = match[1].split("|");
        var label = (pieces[0] || "").trim();
        var reply = (pieces[1] || label).trim();
        var provider = reply.match(/^provider\.([a-z0-9_-]+):/i);
        var option = { label: label, reply: reply, recommended: /recommended/i.test(label) };
        if (provider) {
          var providerKey = provider[1].toLowerCase();
          providers[providerKey] = providers[providerKey] || [];
          providers[providerKey].push(option);
        } else if (/\b(postgres(?:ql)?|clickhouse|mariadb|mysql|redis|mongodb)\b/i.test(reply + " " + label) && !/\b(option\s*\d+|image|git|source|railpack)\b/i.test(reply)) {
          var matchedDb = (reply + " " + label).match(/\b(postgres(?:ql)?|clickhouse|mariadb|mysql|redis|mongodb)\b/i);
          var dbKey = (matchedDb ? matchedDb[1] : "database").toLowerCase();
          if (dbKey === "postgres") dbKey = "postgresql";
          providers[dbKey] = providers[dbKey] || [];
          providers[dbKey].push(option);
        } else {
          deployment.push(option);
        }
      }
      var inputRe = /\[INPUT:([^\]]+)\]/gi;
      while ((match = inputRe.exec(text)) !== null) {
        var inputParts = match[1].split("|");
        var key = (inputParts[0] || "").trim().toLowerCase();
        if (!key || SECRET_INPUT.test(key)) continue;
        var reqPart = (inputParts[3] || "").trim().toLowerCase();
        var isRequired = reqPart === "required" || (/^(?:admin_email|admin_username)$/i.test(key) && reqPart !== "optional");
        inputs.push({
          type: "input",
          key: key,
          label: (inputParts[2] || key.replace(/_/g, " ")).trim(),
          placeholder: (inputParts[1] || "").trim(),
          required: isRequired,
        });
      }
      var steps = [];
      if (deployment.length) steps.push({ type: "options", key: "deployment_method", title: "Deployment method", options: deployment });
      Object.keys(providers).forEach(function (kind) {
        steps.push({ type: "options", key: "provider." + kind, title: "Provider for " + kind, options: providers[kind] });
      });
      return { steps: steps.concat(inputs) };
    },

    _render: function () {
      if (!this.containerEl || !this.steps.length) return;
      if (this.index >= this.steps.length) {
        this._complete();
        return;
      }
      var self = this;
      var step = this.steps[this.index];
      var total = this.steps.length;
      var stepNum = this.index + 1;
      var isLast = stepNum === total;

      var rawValue = self.answers[step.key] || (step.type === "options" ? self._defaultOption(step.options).reply : "");
      if (step.type === "options" && !self.answers[step.key]) self.answers[step.key] = rawValue;

      var html = [
        '<div class="ai-decision-card" style="background:var(--color-surface,#1e1e24);border:1px solid var(--color-line,rgba(255,255,255,.12));border-radius:8px;padding:12px 14px;margin:6px 0;color:var(--color-text,#f1f5f9);box-shadow:0 4px 12px rgba(0,0,0,0.25);">',
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--color-line,rgba(255,255,255,.08));">',
        '<span style="font-size:11px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;color:var(--color-accent,#818cf8);">Step ' + stepNum + ' of ' + total + '</span>',
        '<span style="font-size:11px;color:var(--color-muted,#94a3b8);">' + self._escape(step.title || step.label || "Setup Option") + '</span>',
        '</div>',
      ];

      html.push('<div style="margin-bottom:12px;">');
      if (step.type === "options") {
        html.push('<div style="font-size:12px;font-weight:600;color:var(--color-text,#f8fafc);margin-bottom:8px;">Select ' + self._escape(step.title || "Option") + ':</div>');
        html.push('<div style="display:flex;flex-direction:column;gap:5px;">');
        step.options.forEach(function (option) {
          var selected = option.reply === rawValue;
          var optBg = selected ? "rgba(99,102,241,0.22)" : "var(--color-bg,#121216)";
          var optBorder = selected ? "1px solid var(--color-accent,#6366f1)" : "1px solid var(--color-line,rgba(255,255,255,.08))";
          var checkMark = selected
            ? '<span style="color:var(--color-accent,#818cf8);font-weight:700;margin-right:6px;">●</span>'
            : '<span style="color:var(--color-muted,#64748b);margin-right:6px;">○</span>';
          var recBadge = option.recommended
            ? ' <span style="font-size:10px;background:rgba(99,102,241,0.25);color:#a5b4fc;padding:1px 6px;border-radius:10px;margin-left:6px;font-weight:600;">Recommended</span>'
            : '';
          html.push('<button type="button" class="ai-setup-option" data-step-key="' + self._escape(step.key) + '" data-reply="' + self._escape(option.reply) + '" style="display:flex;align-items:center;padding:7px 10px;background:' + optBg + ';border:' + optBorder + ';border-radius:6px;color:inherit;text-align:left;cursor:pointer;font-size:12px;transition:all .12s ease;">' + checkMark + '<span style="flex:1;">' + self._escape(option.label.replace(/\s*\(Recommended\)/i, "")) + '</span>' + recBadge + '</button>');
        });
        html.push('</div>');
      } else {
        var inputType = /email/i.test(step.key + " " + step.label) ? "email" : "text";
        var val = rawValue === "[skip]" ? "" : (rawValue || step.placeholder || "");
        var badge = step.required
          ? ' <span style="font-size:10px;color:var(--color-danger,#f87171);font-weight:600;">* Required</span>'
          : ' <span style="font-size:10px;color:var(--color-muted,#94a3b8);font-weight:normal;">(Optional)</span>';
        var reqAttr = step.required ? ' required' : '';
        html.push('<label style="display:flex;flex-direction:column;gap:6px;font-size:12px;font-weight:500;">');
        html.push('<span>' + self._escape(step.label) + badge + '</span>');
        html.push('<input class="ai-setup-input"' + reqAttr + ' type="' + inputType + '" data-key="' + self._escape(step.key) + '" value="' + self._escape(val) + '" placeholder="' + self._escape(step.placeholder) + '" style="background:var(--color-bg,#0f172a);border:1px solid var(--color-line,rgba(255,255,255,.15));border-radius:6px;padding:8px 10px;color:inherit;font-size:12.5px;outline:none;transition:border-color .15s;" />');
        html.push('</label>');
      }
      html.push('</div>');

      // Step Navigation Actions
      html.push('<div style="display:flex;gap:8px;align-items:center;justify-content:space-between;margin-top:8px;">');
      if (this.index > 0) {
        html.push('<button type="button" class="ai-setup-back-btn" style="background:transparent;border:1px solid var(--color-line,rgba(255,255,255,.12));color:var(--color-muted,#94a3b8);border-radius:5px;padding:6px 12px;cursor:pointer;font-size:11.5px;font-weight:500;">← Back</button>');
      } else {
        html.push('<span></span>');
      }

      html.push('<div style="display:flex;gap:6px;align-items:center;">');
      if (step.type === "input" && !step.required) {
        html.push('<button type="button" class="ai-setup-skip" style="background:transparent;border:1px solid var(--color-line,rgba(255,255,255,.12));color:var(--color-muted,#94a3b8);border-radius:5px;padding:6px 12px;cursor:pointer;font-size:11.5px;">Skip</button>');
      }

      var nextLabel = isLast ? 'Finish & Propose Plan ✓' : 'Continue →';
      html.push('<button type="button" class="ai-setup-submit-btn" style="background:var(--color-accent,#6366f1);color:#fff;border:0;border-radius:5px;padding:6px 16px;cursor:pointer;font-size:11.5px;font-weight:600;display:flex;align-items:center;gap:4px;">');
      html.push('<span>' + nextLabel + '</span>');
      html.push('</button>');
      html.push('</div>');
      html.push('</div>');

      html.push('</div>');

      this.containerEl.innerHTML = html.join("\n");
      this.containerEl.style.display = "block";

      // Wire option clicks
      this.containerEl.querySelectorAll(".ai-setup-option").forEach(function (button) {
        button.addEventListener("click", function () {
          var stepKey = button.getAttribute("data-step-key") || "";
          var reply = button.getAttribute("data-reply") || "";
          self.answers[stepKey] = reply;
          if (self.index + 1 < self.steps.length) {
            self.index++;
            self._render();
          } else {
            self._complete();
          }
        });
      });

      // Wire back button
      var backBtn = this.containerEl.querySelector(".ai-setup-back-btn");
      if (backBtn) {
        backBtn.addEventListener("click", function () {
          if (self.index > 0) {
            self.index--;
            self._render();
          }
        });
      }

      // Wire skip button
      var skipBtn = this.containerEl.querySelector(".ai-setup-skip");
      if (skipBtn) {
        skipBtn.addEventListener("click", function () {
          self.answers[step.key] = "[skip]";
          if (self.index + 1 < self.steps.length) {
            self.index++;
            self._render();
          } else {
            self._complete();
          }
        });
      }

      // Wire submit / continue button
      var submitBtn = this.containerEl.querySelector(".ai-setup-submit-btn");
      var advanceCurrentStep = function () {
        if (step.type === "input") {
          var input = self.containerEl.querySelector(".ai-setup-input");
          if (input) {
            if (step.required && !input.reportValidity()) return;
            var val = input.value.trim();
            self.answers[step.key] = val || (step.required ? "" : "[skip]");
          }
        }
        if (self.index + 1 < self.steps.length) {
          self.index++;
          self._render();
        } else {
          self._complete();
        }
      };

      if (submitBtn) {
        submitBtn.addEventListener("click", advanceCurrentStep);
      }

      // Allow pressing Enter in input field
      var currentInput = this.containerEl.querySelector(".ai-setup-input");
      if (currentInput) {
        currentInput.focus();
        currentInput.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            e.preventDefault();
            advanceCurrentStep();
          }
        });
      }
    },

    _saveAllInputs: function () {
      var self = this;
      var valid = true;
      this.containerEl.querySelectorAll(".ai-setup-input").forEach(function (input) {
        var key = input.getAttribute("data-key");
        var step = self.steps.filter(function (s) { return s.key === key; })[0];
        if (step && step.required && !input.reportValidity()) {
          valid = false;
          return;
        }
        var trimmed = input.value.trim();
        self.answers[key] = trimmed || (step && step.required ? "" : "[skip]");
      });
      return valid;
    },

    _complete: function () {
      if (this.completed) return;
      this.completed = true;
      var lines = ["Setup interview answers:"];
      var self = this;
      this.steps.forEach(function (step) {
        var rawVal = self.answers[step.key] || "[skip]";
        if (typeof rawVal === "string" && rawVal.indexOf(step.key + ":") === 0) {
          rawVal = rawVal.substring(step.key.length + 1).trim();
        }
        lines.push(step.key + ": " + rawVal);
      });
      this.hide();
      if (typeof this.onComplete === "function") this.onComplete(lines.join("\n"));
    },

    _defaultOption: function (options) {
      return options.filter(function (option) { return option.recommended; })[0] || options[0];
    },

    hide: function () {
      if (this.containerEl) { this.containerEl.style.display = "none"; this.containerEl.innerHTML = ""; }
    },

    _escape: function (value) {
      return String(value || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    },
  };
  window.AiHelperSetupInterview = interview;
})();
