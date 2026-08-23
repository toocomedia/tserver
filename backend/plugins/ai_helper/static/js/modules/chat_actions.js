/**
 * chat_actions.js — Interaction handlers for chat actions, copy, and reviewed setup cards.
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
        // A setup handoff must remain visible; its accept button cannot sit below a collapsed message.
        var hasSetupPlan = bubble.querySelector(".ai-app-plan-card");
        if (hasSetupPlan) {
          bubble.classList.remove("ai-msg-bubble--collapsible");
          var existingToggle = msgWrap.querySelector(".ai-msg-expand-toggle-btn");
          if (existingToggle) existingToggle.remove();
          return;
        }
        var hasStructuredCards = bubble.querySelector(".ai-table-wrap, .ai-security-card");
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

    monitorDeploymentInChat: function (appId, deploymentId, containerEl) {
      if (!containerEl) return;

      var streamBox = containerEl.querySelector(".ai-live-deployment-stream");
      if (!streamBox) {
        streamBox = document.createElement("div");
        streamBox.className = "ai-live-deployment-stream";
        streamBox.style.cssText = "margin-top: 12px; background: rgba(0, 0, 0, 0.45); border: 1px solid var(--color-line, rgba(255,255,255,0.12)); border-radius: 8px; padding: 10px; font-family: monospace; font-size: 11px; line-height: 1.4;";
        streamBox.innerHTML = [
          '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 6px;">',
          '  <span style="font-weight: 600; color: var(--color-text, #fff); display: flex; align-items: center; gap: 6px;">',
          '    <span class="step-spinner" style="width: 10px; height: 10px; border-width: 2px; display: inline-block;"></span> Live Build Log #' + deploymentId,
          '  </span>',
          '  <span class="badge badge--accent ai-stream-stage" style="font-size: 10px; text-transform: uppercase;">QUEUED</span>',
          '</div>',
          '<pre class="ai-stream-logs" style="max-height: 180px; overflow-y: auto; margin: 0; white-space: pre-wrap; word-break: break-all; color: #a6accd; font-size: 11px; padding: 4px 0;">Starting deployment...</pre>',
          '<div class="ai-stream-footer" style="margin-top: 8px; display: flex; justify-content: space-between; align-items: center; font-size: 11px;">',
          '  <a href="/plugins/railpack_apps/' + encodeURIComponent(appId) + '?deployment=' + encodeURIComponent(deploymentId) + '#deployment" target="_blank" style="color: var(--color-accent, #6366f1); text-decoration: none; font-weight: 500;">Open Live Page ↗</a>',
          '</div>'
        ].join("");
        containerEl.appendChild(streamBox);
      }

      var stageBadge = streamBox.querySelector(".ai-stream-stage");
      var logsPre = streamBox.querySelector(".ai-stream-logs");
      var spinner = streamBox.querySelector(".step-spinner");

      function pollStream() {
        fetch("/plugins/railpack_apps/" + encodeURIComponent(appId) + "/deployments/" + encodeURIComponent(deploymentId))
          .then(function (res) {
            if (!res.ok) throw new Error("Status " + res.status);
            return res.json();
          })
          .then(function (item) {
            if (stageBadge) {
              stageBadge.textContent = (item.status || "RUNNING") + " · " + (item.stage || "BUILD");
              if (item.status === "success") {
                stageBadge.className = "badge badge--ok";
                stageBadge.textContent = "SUCCESS · RUNNING";
                if (spinner) spinner.style.display = "none";
              } else if (item.status === "failed") {
                stageBadge.className = "badge badge--danger";
                stageBadge.textContent = "FAILED · " + (item.stage || "ERROR");
                if (spinner) spinner.style.display = "none";
              }
            }
            if (logsPre) {
              var text = (item.output || "") + (item.error ? "\n[error] " + item.error : "");
              logsPre.textContent = text || "Waiting for build output...";
              logsPre.scrollTop = logsPre.scrollHeight;
            }

            if (["queued", "running"].indexOf(item.status) !== -1) {
              setTimeout(pollStream, 1500);
            } else {
              if (item.status === "success") {
                if (window.toast) window.toast("Application deployment completed successfully!", "success");
              } else if (item.status === "failed") {
                if (window.toast) window.toast("Deployment failed: " + (item.error || "See logs"), "error");
              }
            }
          })
          .catch(function () {
            setTimeout(pollStream, 3000);
          });
      }

      pollStream();
    },

    init: function (containerEl) {
      var self = this;
      if (!containerEl) return;

      containerEl.addEventListener("click", function (e) {
        // 0. Interactive Quick Option / Decision Chip
        var quickOptBtn = e.target.closest(".ai-quick-option-btn");
        if (quickOptBtn) {
          e.preventDefault();
          var replyText = quickOptBtn.getAttribute("data-reply") || quickOptBtn.textContent.trim();
          if (replyText) {
            quickOptBtn.classList.add("is-selected");
            var parentGroup = quickOptBtn.closest(".ai-quick-options-group");
            if (parentGroup) {
              parentGroup.querySelectorAll(".ai-quick-option-btn").forEach(function (b) {
                if (b !== quickOptBtn) b.disabled = true;
              });
            }
            if (window.AiHelper && typeof window.AiHelper.send === "function") {
              window.AiHelper.send(replyText);
            }
          }
          return;
        }

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

        // Safe App Engine setup handoff. This only opens the prefilled wizard.
        var applyPlanBtn = e.target.closest("[data-action='APP_SETUP_PLAN']");
        if (applyPlanBtn) {
          e.preventDefault();
          var actionType = applyPlanBtn.getAttribute("data-action") || "APP_SETUP_PLAN";
          var setupPlanId = applyPlanBtn.getAttribute("data-plan-id");
          if (!setupPlanId) return;

          applyPlanBtn.disabled = true;
          applyPlanBtn.innerHTML = '<span class="ai-btn-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg></span> <span class="ai-btn-text">Loading Reviewed Setup...</span>';

          if (typeof window.applyAiAppPlan === "function") {
            fetch("/plugins/ai_helper/api/action-plans/" + encodeURIComponent(setupPlanId))
              .then(function (res) {
                if (!res.ok) throw new Error("Plan not found or expired.");
                return res.json();
              })
              .then(function (data) {
                if (!data.plan || (data.plan.action_type !== "app_install" && data.plan.action_type !== "stack_install" && data.plan.action_type !== "official_stack_install")) {
                  throw new Error("Invalid setup plan.");
                }
                window.applyAiAppPlan(data.plan);
                applyPlanBtn.innerHTML = '<span class="ai-btn-icon">✓</span> <span class="ai-btn-text">Setup Loaded & Ready to Deploy</span>';
                applyPlanBtn.classList.add("is-applied");
                if (window.toast) window.toast("Configuration plan loaded into setup wizard.", "success");
              })
              .catch(function (err) {
                applyPlanBtn.disabled = false;
                applyPlanBtn.innerHTML = '<span class="ai-btn-text">Apply Reviewed Setup</span> <span class="ai-btn-arrow">→</span>';
                if (window.toast) window.toast(err.message, "error");
              });
            return;
          }
          window.location.href = "/plugins/railpack_apps/create?plan=" + encodeURIComponent(setupPlanId);
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


        // 6b. Server-verified sensitive-file unlock — grants session consent after a user click.
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
