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
        // 1. Code View / Expand button
        var expandBtn = e.target.closest(".ai-code-expand-btn, [data-ai-code-view]");
        if (expandBtn) {
          e.preventDefault();
          var block = expandBtn.closest(".ai-code-block");
          if (block) {
            var codeTag = block.querySelector("code");
            var lang = block.getAttribute("data-lang") || "text";
            if (codeTag && window.AiHelperCodeView) {
              window.AiHelperCodeView.open(codeTag.innerText, lang);
            }
          }
          return;
        }

        // 2. Code block copy button
        var copyBtn = e.target.closest(".ai-code-copy-btn");
        if (copyBtn) {
          e.preventDefault();
          var blockForCopy = copyBtn.closest(".ai-code-block");
          var codeEl = blockForCopy ? blockForCopy.querySelector("code") : copyBtn.parentElement.querySelector("code");
          if (codeEl) {
            self.copyToClipboard(codeEl.innerText, copyBtn);
          }
          return;
        }

        // 3. Thought Process Box toggle
        var thoughtHeader = e.target.closest(".ai-thought-header");
        if (thoughtHeader) {
          e.preventDefault();
          var thoughtBox = thoughtHeader.closest(".ai-thought-box");
          if (thoughtBox) {
            var currentState = thoughtBox.getAttribute("data-state") || "collapsed";
            var nextState = currentState === "expanded" ? "collapsed" : "expanded";
            thoughtBox.setAttribute("data-state", nextState);
            var chevron = thoughtBox.querySelector(".ai-thought-chevron");
            if (chevron) {
              chevron.textContent = nextState === "expanded" ? "▴" : "▾";
            }
          }
          return;
        }

        // 4. Checklist Item toggle
        var checkItem = e.target.closest(".ai-checklist-item");
        if (checkItem) {
          var isChecked = checkItem.classList.contains("ai-checklist-item--checked");
          if (isChecked) {
            checkItem.classList.remove("ai-checklist-item--checked");
            var icon1 = checkItem.querySelector(".ai-check-icon");
            if (icon1) icon1.textContent = "○";
          } else {
            checkItem.classList.add("ai-checklist-item--checked");
            var icon2 = checkItem.querySelector(".ai-check-icon");
            if (icon2) icon2.textContent = "✓";
          }
          return;
        }

        // 5. Interactive Action Tag
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
