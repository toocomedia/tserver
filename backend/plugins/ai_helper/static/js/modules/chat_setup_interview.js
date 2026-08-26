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
      if (/\[ACTION:APP_SETUP_PLAN:/i.test(text) || /ready to deploy/i.test(text)) {
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
      this.answers = {};
      this.completed = false;
      this._render();
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
      var step = this.steps[this.index];
      var total = this.steps.length;
      var current = this.index + 1;
      var rawValue = this.answers[step.key] || (step.type === "options" ? this._defaultOption(step.options).reply : "");
      if (step.type === "options" && !this.answers[step.key]) this.answers[step.key] = rawValue;
      var html = [
        '<div class="ai-decision-card" style="background:var(--color-surface,#1a1a1a);border-radius:6px;padding:8px 10px;margin:4px 6px 6px;color:var(--color-text,#fff);">',
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">',
        '<span style="font-size:11.5px;font-weight:600;color:var(--color-text,#fff);">' + this._escape(step.title || step.label || "Configuration") + '</span>',
        '<span style="font-size:10.5px;color:var(--color-muted,#94a3b8);">' + current + " / " + total + "</span>",
        "</div>",
      ];
      if (step.type === "options") {
        html.push('<div style="display:flex;flex-direction:column;gap:4px;">');
        step.options.forEach(function (option) {
          var selected = option.reply === rawValue;
          var optBg = selected ? "var(--color-surface-hover,rgba(99,102,241,0.15))" : "var(--color-bg,#121212)";
          var optBorder = selected ? "1px solid var(--color-accent,#6366f1)" : "1px solid transparent";
          html.push('<button type="button" class="ai-setup-option" data-reply="' + self._escape(option.reply) + '" style="display:flex;align-items:center;padding:5px 8px;background:' + optBg + ';border:' + optBorder + ';border-radius:4px;color:inherit;text-align:left;cursor:pointer;font-size:12px;transition:background .1s ease;">' + self._escape(option.label) + "</button>");
        });
        html.push("</div>");
      } else {
        var inputType = /email/i.test(step.key + " " + step.label) ? "email" : "text";
        var val = rawValue === "[skip]" ? "" : rawValue;
        var badge = step.required
          ? ' <span style="font-size:10px;color:var(--color-danger,#ef4444);font-weight:600;">* Required</span>'
          : ' <span style="font-size:10px;color:var(--color-muted,#94a3b8);font-weight:normal;">(Optional)</span>';
        var reqAttr = step.required ? ' required' : '';
        html.push('<label style="display:flex;flex-direction:column;gap:4px;font-size:11.5px;">' + this._escape(step.label) + badge + '<input class="ai-setup-input"' + reqAttr + ' type="' + inputType + '" data-key="' + this._escape(step.key) + '" value="' + this._escape(val) + '" placeholder="' + this._escape(step.placeholder) + '" style="background:var(--color-bg,#121212);border:1px solid var(--color-line,rgba(255,255,255,.1));border-radius:4px;padding:5px 8px;color:inherit;font-size:12px;outline:none;" /></label>');
      }
      html.push('<div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;">');
      html.push('<button type="button" class="ai-setup-back"' + (this.index ? "" : " disabled") + ' style="background:transparent;border:0;color:var(--color-muted,#94a3b8);cursor:pointer;font-size:11.5px;padding:2px 4px;">Back</button>');
      html.push('<div style="display:flex;gap:6px;align-items:center;">');
      if (step.type === "input" && !step.required) {
        html.push('<button type="button" class="ai-setup-skip" style="background:transparent;border:0;color:var(--color-muted,#94a3b8);cursor:pointer;font-size:11.5px;padding:4px 8px;">Skip</button>');
      }
      html.push('<button type="button" class="ai-setup-next" style="background:var(--color-accent,#6366f1);color:#fff;border:0;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:11.5px;font-weight:500;">' + (current === total ? "Send" : "Continue") + "</button>");
      html.push("</div></div></div>");
      this.containerEl.innerHTML = html.join("\n");
      this.containerEl.style.display = "block";
      this.containerEl.querySelectorAll(".ai-setup-option").forEach(function (button) {
        button.addEventListener("click", function () {
          self.answers[step.key] = button.getAttribute("data-reply") || "";
          self._render();
        });
      });
      this.containerEl.querySelector(".ai-setup-back").addEventListener("click", function () {
        self._saveCurrent();
        if (self.index) { self.index -= 1; self._render(); }
      });
      var skipBtn = this.containerEl.querySelector(".ai-setup-skip");
      if (skipBtn) {
        skipBtn.addEventListener("click", function () {
          self.answers[step.key] = "[skip]";
          if (self.index + 1 < self.steps.length) { self.index += 1; self._render(); return; }
          self._complete();
        });
      }
      var next = function () {
        if (!self._saveCurrent()) return;
        if (self.index + 1 < self.steps.length) { self.index += 1; self._render(); return; }
        self._complete();
      };
      this.containerEl.querySelector(".ai-setup-next").addEventListener("click", next);
      var input = this.containerEl.querySelector(".ai-setup-input");
      if (input) input.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); next(); } });
    },

    _saveCurrent: function () {
      var step = this.steps[this.index];
      var input = this.containerEl.querySelector(".ai-setup-input");
      if (!input) return true;
      if (step.required && !input.reportValidity()) return false;
      var trimmed = input.value.trim();
      if (step.required && !trimmed) return false;
      this.answers[step.key] = trimmed || (step.required ? "" : "[skip]");
      return true;
    },

    _complete: function () {
      if (this.completed) return;
      this.completed = true;
      var lines = ["Setup interview answers:"];
      this.steps.forEach(function (step) { lines.push(step.key + ": " + (interview.answers[step.key] || "[skip]")); });
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
