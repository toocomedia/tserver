/**
 * chat_actions.js — Action Registry & Interactive Tool Handlers for AI Assistant Chat Room.
 * Extensible system for action tags, code snippet actions, and panel integrations.
 */
(function () {
  "use strict";

  var handlers = {};

  var AiHelperActions = {
    register: function (actionType, handlerFn) {
      if (actionType && typeof handlerFn === "function") {
        handlers[actionType.toUpperCase()] = handlerFn;
      }
    },

    execute: function (actionType, actionVal, element) {
      var key = (actionType || "").toUpperCase();
      if (handlers[key]) {
        return handlers[key](actionVal, element);
      }
      // Default fallback: copy to clipboard
      this.copyToClipboard(actionVal, element);
    },

    copyToClipboard: function (text, triggerEl) {
      if (!text) return;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          AiHelperActions._flashSuccess(triggerEl, "Copied!");
        });
      } else {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand("copy");
          AiHelperActions._flashSuccess(triggerEl, "Copied!");
        } catch (e) {}
        document.body.removeChild(ta);
      }
    },

    _flashSuccess: function (el, msg) {
      if (!el) return;
      var origText = el.getAttribute("data-orig-text") || el.textContent;
      if (!el.getAttribute("data-orig-text")) el.setAttribute("data-orig-text", origText);
      el.textContent = msg;
      setTimeout(function () {
        el.textContent = origText;
      }, 1500);
    },

    init: function (containerEl) {
      var self = this;
      if (!containerEl) return;

      containerEl.addEventListener("click", function (e) {
        // 1. Code block copy button
        var copyBtn = e.target.closest(".ai-code-copy-btn");
        if (copyBtn) {
          var codeEl = copyBtn.parentElement.querySelector("code");
          if (codeEl) {
            self.copyToClipboard(codeEl.innerText, copyBtn);
          }
          return;
        }

        // 2. Interactive Action Tag
        var tag = e.target.closest(".ai-action-tag");
        if (tag) {
          var actionType = tag.getAttribute("data-action");
          var actionVal = tag.getAttribute("data-copy") || tag.textContent;
          self.execute(actionType, actionVal, tag);
          return;
        }
      });
    },
  };

  // Register common built-in action handlers
  AiHelperActions.register("COPY", function (val, el) {
    AiHelperActions.copyToClipboard(val, el);
  });

  AiHelperActions.register("SET_PORT", function (val, el) {
    AiHelperActions.copyToClipboard(val, el);
    var portInput = document.querySelector('input[name="target_port"], input[name="port"], input[name="container_port"]');
    if (portInput) {
      portInput.value = val;
      portInput.dispatchEvent(new Event("input", { bubbles: true }));
      AiHelperActions._flashSuccess(el, "Port Set!");
    }
  });

  window.AiHelperActions = AiHelperActions;
})();
