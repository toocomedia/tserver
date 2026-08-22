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
        // Do not prematurely collapse messages containing action plans or tables
        var hasStructuredCards = bubble.querySelector(".ai-app-plan-card, .ai-table-wrap, .ai-security-card");
        var threshold = hasStructuredCards ? 1200 : 700;
        if (bubble.scrollHeight > threshold && !msgWrap.querySelector(".ai-msg-expand-toggle-btn")) {
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

        // 6a. APP_PLAN / APP_NEXT / APP_DEPLOY Action Buttons — apply plan to wizard and guide step-by-step
        var applyPlanBtn = e.target.closest(".ai-action-btn--apply-plan, .ai-action-btn--big-next, [data-action='APP_PLAN'], [data-action='APP_NEXT'], [data-action='APP_DEPLOY']");
        if (applyPlanBtn) {
          e.preventDefault();
          var actionType = applyPlanBtn.getAttribute("data-action") || "APP_PLAN";

          if (actionType === "APP_NEXT") {
            if (typeof window.advanceAiWizard === "function") {
              window.advanceAiWizard();
              applyPlanBtn.innerHTML = "✓ Step Accepted";
              applyPlanBtn.classList.add("is-applied");
            }
            return;
          }

          if (actionType === "APP_DEPLOY" || actionType === "APP_REBUILD") {
            applyPlanBtn.disabled = true;
            applyPlanBtn.innerHTML = '<span class="ai-btn-icon">⏳</span> Deploying Application...';
            applyPlanBtn.classList.add("is-applied");
            if (typeof window.startAiDeployment === "function") {
              Promise.resolve(window.startAiDeployment())
                .then(function () {
                  applyPlanBtn.innerHTML = '<span class="ai-btn-icon">✓</span> Deployment Started';
                })
                .catch(function (err) {
                  applyPlanBtn.disabled = false;
                  applyPlanBtn.classList.remove("is-applied");
                  applyPlanBtn.innerHTML = '<span class="ai-btn-icon">🚀</span> Retry Deploy Application <span class="ai-btn-arrow">→</span>';
                  if (err && err.message && window.toast) {
                    window.toast(err.message, "error");
                  }
                });
            }
            return;
          }

          var planId = applyPlanBtn.getAttribute("data-plan-id");
          if (!planId) return;
          applyPlanBtn.disabled = true;
          applyPlanBtn.textContent = "Loading configuration...";

          fetch("/plugins/ai_helper/api/action-plans/" + encodeURIComponent(planId))
            .then(function (res) {
              if (!res.ok) throw new Error("Plan not found or expired.");
              return res.json();
            })
            .then(function (data) {
              var plan = data.plan;
              if (!plan || !plan.payload) throw new Error("Invalid plan data.");

              // If currently on Apps Engine Create page: apply directly to wizard
              if (window.applyAiAppPlan && typeof window.applyAiAppPlan === "function") {
                window.applyAiAppPlan(plan, { autoAdvance: true });
                // Transform button to big Deploy CTA
                applyPlanBtn.disabled = false;
                applyPlanBtn.setAttribute("data-action", "APP_DEPLOY");
                applyPlanBtn.className = "ai-action-btn--big-next ai-action-btn--deploy";
                applyPlanBtn.innerHTML = '<span class="ai-btn-icon">🚀</span> Accept & Deploy Application <span class="ai-btn-arrow">→</span>';
                // Do NOT close AI Helper — keep 60/40 split view open for live deployment guidance
              } else {
                // Redirect to create page with plan param
                window.location.href = "/plugins/railpack_apps/create?plan=" + encodeURIComponent(planId);
              }
            })
            .catch(function (err) {
              // If on wizard create page, advance wizard to inspect/configure anyway
              if (typeof window.advanceAiWizard === "function") {
                window.advanceAiWizard();
                applyPlanBtn.disabled = false;
                applyPlanBtn.setAttribute("data-action", "APP_DEPLOY");
                applyPlanBtn.className = "ai-action-btn--big-next ai-action-btn--deploy";
                applyPlanBtn.innerHTML = '<span class="ai-btn-icon">🚀</span> Accept & Deploy Application <span class="ai-btn-arrow">→</span>';
              } else {
                applyPlanBtn.disabled = false;
                applyPlanBtn.textContent = "Error: " + err.message;
              }
            });
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
              secretsBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px;"><polyline points="20 6 9 17 4 12"></polyline></svg> Credentials Unlocked';
              secretsBtn.classList.add("ai-action-tag--secrets-granted");
              // Trigger the AI to re-run the previous request with secrets consent
              if (window.AiHelper && window.AiHelper.sendMessage) {
                window.AiHelper.sendMessage("I allow secrets — please re-run your last file check.");
              }
            })
            .catch(function () {
              secretsBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg> Failed to unlock';
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

        // 6f. File Tree Expand/Collapse button (>5 items)
        var expandFilesBtn = e.target.closest(".ai-file-expand-btn");
        if (expandFilesBtn) {
          e.preventDefault();
          var card = expandFilesBtn.closest(".ai-file-tree-card");
          if (card) {
            var isExp = card.classList.contains("ai-file-tree-card--expanded");
            var hiddenCount = expandFilesBtn.getAttribute("data-hidden-count") || "";
            if (isExp) {
              card.classList.remove("ai-file-tree-card--expanded");
              expandFilesBtn.textContent = "Show " + hiddenCount + " more items ▾";
            } else {
              card.classList.add("ai-file-tree-card--expanded");
              expandFilesBtn.textContent = "Show less ▴";
            }
          }
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
