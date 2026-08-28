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
      var self = this;
      var html = [
        '<div class="ai-decision-card" style="background:var(--color-surface,#1a1a1a);border:1px solid var(--color-line,rgba(255,255,255,.1));border-radius:6px;padding:10px 12px;margin:4px 0;color:var(--color-text,#fff);">',
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">',
        '<span style="font-size:11.5px;font-weight:600;letter-spacing:0.3px;color:var(--color-text,#fff);">Configuration Settings</span>',
        '<span style="font-size:10px;color:var(--color-muted,#94a3b8);">' + this.steps.length + ' fields</span>',
        '</div>',
      ];

      this.steps.forEach(function (step) {
        var rawValue = self.answers[step.key] || (step.type === "options" ? self._defaultOption(step.options).reply : "");
        if (step.type === "options" && !self.answers[step.key]) self.answers[step.key] = rawValue;

        html.push('<div style="margin-bottom:8px;">');
        html.push('<div style="font-size:10.5px;font-weight:600;color:var(--color-muted,#94a3b8);margin-bottom:4px;text-transform:uppercase;letter-spacing:0.5px;">' + self._escape(step.title || step.label || "Configuration") + '</div>');

        if (step.type === "options") {
          html.push('<div style="display:flex;flex-direction:column;gap:3px;">');
          step.options.forEach(function (option) {
            var selected = option.reply === rawValue;
            var optBg = selected ? "var(--color-surface-hover,rgba(99,102,241,0.18))" : "var(--color-bg,#121212)";
            var optBorder = selected ? "1px solid var(--color-accent,#6366f1)" : "1px solid var(--color-line,rgba(255,255,255,.07))";
            var checkMark = selected ? '<span style="color:var(--color-accent,#6366f1);font-weight:600;margin-right:6px;"></span>' : '<span style="opacity:0;margin-right:6px;"></span>';
            html.push('<button type="button" class="ai-setup-option" data-step-key="' + self._escape(step.key) + '" data-reply="' + self._escape(option.reply) + '" style="display:flex;align-items:center;padding:5px 8px;background:' + optBg + ';border:' + optBorder + ';border-radius:4px;color:inherit;text-align:left;cursor:pointer;font-size:11.5px;transition:background .1s ease;">' + checkMark + self._escape(option.label) + "</button>");
          });
          html.push('</div>');
        } else {
          var inputType = /email/i.test(step.key + " " + step.label) ? "email" : "text";
          var val = rawValue === "[skip]" ? "" : rawValue;
          var badge = step.required
            ? ' <span style="font-size:10px;color:var(--color-danger,#ef4444);font-weight:600;">* Required</span>'
            : ' <span style="font-size:10px;color:var(--color-muted,#94a3b8);font-weight:normal;">(Optional)</span>';
          var reqAttr = step.required ? ' required' : '';
          html.push('<label style="display:flex;flex-direction:column;gap:4px;font-size:11.5px;">' + self._escape(step.label) + badge + '<input class="ai-setup-input"' + reqAttr + ' type="' + inputType + '" data-key="' + self._escape(step.key) + '" value="' + self._escape(val) + '" placeholder="' + self._escape(step.placeholder) + '" style="background:var(--color-bg,#121212);border:1px solid var(--color-line,rgba(255,255,255,.1));border-radius:4px;padding:5px 8px;color:inherit;font-size:12px;outline:none;" /></label>');
        }
        html.push('</div>');
      });

      html.push('<button type="button" class="ai-setup-submit-btn" style="background:var(--color-accent,#6366f1);color:#fff;border:0;border-radius:4px;padding:7px 14px;cursor:pointer;font-size:11.5px;font-weight:600;width:100%;margin-top:4px;display:flex;align-items:center;justify-content:center;gap:6px;">');
      html.push('<span>Confirm Configuration</span>');
      html.push('<span>→</span>');
      html.push('</button>');
      html.push('</div>');

      this.containerEl.innerHTML = html.join("\n");
      this.containerEl.style.display = "block";

      this.containerEl.querySelectorAll(".ai-setup-option").forEach(function (button) {
        button.addEventListener("click", function () {
          var stepKey = button.getAttribute("data-step-key") || "";
          var reply = button.getAttribute("data-reply") || "";
          self.answers[stepKey] = reply;
          self._render();
        });
      });

      var submitBtn = this.containerEl.querySelector(".ai-setup-submit-btn");
      if (submitBtn) {
        submitBtn.addEventListener("click", function () {
          if (!self._saveAllInputs()) return;
          self._complete();
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
