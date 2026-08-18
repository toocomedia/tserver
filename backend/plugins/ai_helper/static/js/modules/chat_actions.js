/**
 * chat_actions.js — Interaction handlers for chat actions, copy, cards, and thought toggles.
 */
(function () {
  "use strict";

  var AiHelperActions = {
    copyToClipboard: function (text, btnEl) {
      if (!text) return;
      var self = this;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          if (btnEl) self._showCopyFeedback(btnEl);
        });
      } else {
        var textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        try {
          document.execCommand("copy");
          if (btnEl) self._showCopyFeedback(btnEl);
        } catch (e) {}
        document.body.removeChild(textarea);
      }
    },

    _showCopyFeedback: function (el) {
      var origText = el.textContent;
      el.textContent = "Copied!";
      setTimeout(function () {
        el.textContent = origText;
      }, 1500);
    },

    checkLongMessages: function (containerEl) {
      if (!containerEl) return;
      var assistantMsgs = containerEl.querySelectorAll(".ai-msg--assistant");
      assistantMsgs.forEach(function (msgWrap) {
        var bubble = msgWrap.querySelector(".ai-msg-bubble");
        if (!bubble) return;
        // Threshold: 360px height
        if (bubble.scrollHeight > 360 && !msgWrap.querySelector(".ai-msg-expand-toggle-btn")) {
          bubble.classList.add("ai-msg-bubble--collapsible");
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "ai-msg-expand-toggle-btn";
          btn.textContent = "Show more ▾";
          btn.setAttribute("title", "Toggle message expansion");

          var timeEl = msgWrap.querySelector(".ai-msg-time");
          if (timeEl) {
            msgWrap.insertBefore(btn, timeEl);
          } else {
            msgWrap.appendChild(btn);
          }
        }
      });
    },

    init: function (containerEl) {
      var self = this;
      if (!containerEl) return;

      containerEl.addEventListener("click", function (e) {
        // 1. One-Line Card Strip Copy Button
        var copyBtnInStrip = e.target.closest(".ai-card-strip-copy-btn");
        if (copyBtnInStrip) {
          e.preventDefault();
          e.stopPropagation();
          var stripEl = copyBtnInStrip.closest(".ai-card-strip");
          if (stripEl) {
            var rawData = decodeURIComponent(stripEl.getAttribute("data-code") || "");
            self.copyToClipboard(rawData, copyBtnInStrip);
          }
          return;
        }

        // 2. One-Line Card Strip Expand (Open in Split Viewer)
        var cardStrip = e.target.closest(".ai-card-strip-expand-btn, .ai-card-strip");
        if (cardStrip) {
          e.preventDefault();
          var targetStrip = cardStrip.classList.contains("ai-card-strip") ? cardStrip : cardStrip.closest(".ai-card-strip");
          if (targetStrip && window.AiHelperCodeView) {
            var codeText = decodeURIComponent(targetStrip.getAttribute("data-code") || "");
            var lang = targetStrip.getAttribute("data-lang") || "text";
            var title = targetStrip.getAttribute("data-title") || "snippet.txt";
            window.AiHelperCodeView.open(codeText, lang, title);
          }
          return;
        }

        // 3. In-bubble Code Block Collapse / Expand Toggle
        var codeCollapseBtn = e.target.closest(".ai-code-toggle-collapse-btn");
        if (codeCollapseBtn) {
          e.preventDefault();
          var codeBlock = codeCollapseBtn.closest(".ai-code-block");
          if (codeBlock) {
            var curState = codeBlock.getAttribute("data-state") || "collapsed";
            var nextState = curState === "expanded" ? "collapsed" : "expanded";
            codeBlock.setAttribute("data-state", nextState);
            var lineCount = codeBlock.getAttribute("data-lines") || "";
            if (nextState === "expanded") {
              codeCollapseBtn.textContent = "Collapse ▴";
            } else {
              codeCollapseBtn.textContent = "Expand (" + lineCount + " lines) ▾";
            }
          }
          return;
        }

        // 4. Long Message Bubble Collapse / Expand Toggle
        var msgToggleBtn = e.target.closest(".ai-msg-expand-toggle-btn");
        if (msgToggleBtn) {
          e.preventDefault();
          var msgWrap = msgToggleBtn.closest(".ai-msg");
          var bubbleEl = msgWrap ? msgWrap.querySelector(".ai-msg-bubble") : null;
          if (bubbleEl) {
            var isExpanded = bubbleEl.classList.contains("ai-msg-bubble--expanded");
            if (isExpanded) {
              bubbleEl.classList.remove("ai-msg-bubble--expanded");
              msgToggleBtn.textContent = "Show more ▾";
            } else {
              bubbleEl.classList.add("ai-msg-bubble--expanded");
              msgToggleBtn.textContent = "Show less ▴";
            }
          }
          return;
        }

        // 5. Code block copy button
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

        // 6. Action Tag Click — general (Copy or apply value)
        var actionTag = e.target.closest(".ai-action-tag:not(.ai-action-tag--secrets)");
        if (actionTag) {
          e.preventDefault();
          var val = actionTag.getAttribute("data-copy") || actionTag.innerText;
          self.copyToClipboard(val, actionTag);
          return;
        }

        // 6b. ALLOW_SECRETS button — POST to consent API then send follow-up message
        var secretsBtn = e.target.closest(".ai-action-tag--secrets");
        if (secretsBtn) {
          e.preventDefault();
          var sid = secretsBtn.getAttribute("data-session-id") || (window.AiHelper ? window.AiHelper.sessionId : "");
          if (!sid) return;
          fetch("/plugins/ai_helper/api/sessions/" + encodeURIComponent(sid) + "/allow-secrets", { method: "POST" })
            .then(function () {
              // Disable the button so it can't be double-clicked
              secretsBtn.disabled = true;
              secretsBtn.textContent = "\uD83D\uDD13 Credentials Unlocked";
              secretsBtn.classList.add("ai-action-tag--secrets-granted");
              // Trigger the AI to re-run the previous request with secrets consent
              if (window.AiHelper && window.AiHelper.sendMessage) {
                window.AiHelper.sendMessage("I allow secrets — please re-run your last file check.");
              }
            })
            .catch(function () {
              secretsBtn.textContent = "\u26A0\uFE0F Failed to unlock";
            });
          return;
        }

        // 6c. Security card copy button
        var secCopyBtn = e.target.closest(".ai-security-card .ai-card-strip-copy-btn");
        if (secCopyBtn) {
          e.preventDefault();
          var secCard = secCopyBtn.closest(".ai-security-card");
          if (secCard) {
            var rows = secCard.querySelectorAll(".ai-sec-text");
            var lines = [];
            rows.forEach(function (r) { lines.push(r.textContent.trim()); });
            self.copyToClipboard(lines.join("\n"), secCopyBtn);
          }
          return;
        }

        // 6d. File Tree Card Copy All button
        var fileTreeCopyBtn = e.target.closest(".ai-file-tree-copy-btn");
        if (fileTreeCopyBtn) {
          e.preventDefault();
          var card = fileTreeCopyBtn.closest(".ai-file-tree-card");
          if (card) {
            var rawNames = decodeURIComponent(card.getAttribute("data-raw-names") || "");
            self.copyToClipboard(rawNames, fileTreeCopyBtn);
          }
          return;
        }

        // 6e. File Tree Item click — copy single item name or ask AI
        var fileItem = e.target.closest(".ai-file-row, .ai-file-item");
        if (fileItem) {
          e.preventDefault();
          var itemName = fileItem.getAttribute("data-name") || fileItem.querySelector(".ai-file-name").textContent;
          self.copyToClipboard(itemName, fileItem);
          return;
        }

        // 7. Thought Box Header Toggle
        var thoughtHeader = e.target.closest(".ai-thought-header");
        if (thoughtHeader) {
          e.preventDefault();
          var thoughtBox = thoughtHeader.closest(".ai-thought-box");
          if (thoughtBox) {
            var state = thoughtBox.getAttribute("data-state") || "collapsed";
            var chevron = thoughtBox.querySelector(".ai-thought-chevron");
            if (state === "collapsed") {
              thoughtBox.setAttribute("data-state", "expanded");
              if (chevron) chevron.textContent = "▴";
            } else {
              thoughtBox.setAttribute("data-state", "collapsed");
              if (chevron) chevron.textContent = "▾";
            }
          }
          return;
        }

        // 8. Checklist Item Click Toggle
        var checkItem = e.target.closest(".ai-checklist-item");
        if (checkItem) {
          e.preventDefault();
          checkItem.classList.toggle("ai-checklist-item--checked");
          var iconEl = checkItem.querySelector(".ai-check-icon");
          if (iconEl) {
            iconEl.textContent = checkItem.classList.contains("ai-checklist-item--checked") ? "✓" : "○";
          }
        }
      });
    },
  };

  window.AiHelperActions = AiHelperActions;
})();
