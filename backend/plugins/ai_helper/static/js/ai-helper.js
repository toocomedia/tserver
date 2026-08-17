/**
 * ai-helper.js — Universal Vanilla JS Client SDK for SRV / Barq AI Assistant.
 * Clean, modern layout, bottom model selector with multi-model support,
 * large auto-expanding input, live SSE streaming, and Markdown renderer.
 */
(function () {
  "use strict";

  var AiHelper = {
    sessionId: null,
    activeContext: null,
    selectedProviderId: null,
    selectedModelName: null,
    isStreaming: false,
    abortController: null,
    drawerEl: null,
    backdropEl: null,
    messagesEl: null,
    inputEl: null,
    sendBtnEl: null,
    stopBtnEl: null,
    modelPickerEl: null,
    contextBarEl: null,
    contextTextEl: null,
    statusEl: null,

    init: function () {
      if (document.getElementById("ai-helper-drawer")) {
        return; // Already initialized
      }

      this.sessionId = localStorage.getItem("ai_helper_session_id");
      if (!this.sessionId) {
        this.sessionId = "sess_" + Math.random().toString(36).substring(2, 12);
        localStorage.setItem("ai_helper_session_id", this.sessionId);
      }

      var savedSelection = localStorage.getItem("ai_helper_selected_target") || null;
      if (savedSelection && savedSelection.indexOf(":") !== -1) {
        var parts = savedSelection.split(":");
        this.selectedProviderId = parts[0];
        this.selectedModelName = parts.slice(1).join(":");
      }

      this._injectDOM();
      this._bindGlobalTriggers();
      this._loadProviders();
    },

    _injectDOM: function () {
      // 1. Floating Launcher Button
      var floatBtn = document.createElement("button");
      floatBtn.id = "ai-helper-floating-btn";
      floatBtn.className = "ai-helper-floating-btn";
      floatBtn.type = "button";
      floatBtn.setAttribute("aria-label", "Open AI Assistant");
      floatBtn.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg>';
      floatBtn.addEventListener("click", function () {
        AiHelper.toggle();
      });
      document.body.appendChild(floatBtn);

      // 2. Backdrop
      var backdrop = document.createElement("div");
      backdrop.id = "ai-helper-backdrop";
      backdrop.className = "ai-helper-backdrop";
      backdrop.addEventListener("click", function () {
        AiHelper.close();
      });
      document.body.appendChild(backdrop);
      this.backdropEl = backdrop;

      // 3. Drawer Shell
      var drawer = document.createElement("aside");
      drawer.id = "ai-helper-drawer";
      drawer.className = "ai-helper-drawer";
      drawer.innerHTML = [
        '<div class="ai-helper-header">',
        '  <div class="ai-helper-header-info">',
        '    <span class="ai-helper-header-icon">',
        '      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg>',
        "    </span>",
        '    <h3 class="ai-helper-title">AI Assistant</h3>',
        "  </div>",
        '  <div class="ai-helper-header-actions">',
        '    <button type="button" class="ai-helper-btn-icon" id="ai-helper-clear-btn" title="Clear Chat History">',
        '      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>',
        "    </button>",
        '    <button type="button" class="ai-helper-btn-icon" id="ai-helper-close-btn" title="Close">',
        '      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>',
        "    </button>",
        "  </div>",
        "</div>",
        '<div class="ai-helper-context-bar" id="ai-helper-context-bar" style="display: none;">',
        '  <span class="ai-helper-context-text" id="ai-helper-context-text"></span>',
        '  <button type="button" class="ai-helper-btn-icon" style="width:20px;height:20px;" id="ai-helper-clear-context" title="Clear context">✕</button>',
        "</div>",
        '<div class="ai-helper-messages" id="ai-helper-messages">',
        '  <div class="ai-empty-state" id="ai-empty-state">',
        '    <div class="ai-empty-icon">',
        '      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg>',
        "    </div>",
        "    <h4>How can I help?</h4>",
        "    <p>Ask anything about setting up apps, writing Dockerfiles, configuring Nginx, or troubleshooting errors.</p>",
        '    <div class="ai-suggested-prompts">',
        '      <button type="button" class="ai-suggested-item" data-ai-suggest="How do I deploy a Node.js application on this panel?">Deploy a Node.js application</button>',
        '      <button type="button" class="ai-suggested-item" data-ai-suggest="What environment variables do I need for PostgreSQL connection?">PostgreSQL connection variables</button>',
        '      <button type="button" class="ai-suggested-item" data-ai-suggest="Explain common reasons for 502 Bad Gateway errors.">Fix 502 Bad Gateway error</button>',
        "    </div>",
        "  </div>",
        "</div>",
        '<div class="ai-helper-footer">',
        '  <form class="ai-helper-input-box" id="ai-helper-form">',
        '    <textarea class="ai-helper-textarea" id="ai-helper-input" rows="2" placeholder="Ask a question or paste error logs..."></textarea>',
        '    <div class="ai-helper-toolbar">',
        '      <div class="ai-helper-toolbar-left">',
        '        <select class="ai-helper-model-picker" id="ai-helper-model-picker" title="Select AI Model">',
        '          <option value="">Default Model</option>',
        "        </select>",
        '        <span class="ai-helper-status-pill" id="ai-helper-status-model">Ready</span>',
        "      </div>",
        '      <div class="ai-helper-toolbar-right">',
        '        <button type="button" class="ai-helper-stop-btn" id="ai-helper-stop-btn" style="display: none;" title="Stop generation">',
        '          <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg> Stop',
        "        </button>",
        '        <button type="submit" class="ai-helper-send-btn" id="ai-helper-send-btn" title="Send message (Enter)">',
        '          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>',
        "        </button>",
        "      </div>",
        "    </div>",
        "  </form>",
        "</div>",
      ].join("\n");

      document.body.appendChild(drawer);

      this.drawerEl = drawer;
      this.messagesEl = document.getElementById("ai-helper-messages");
      this.inputEl = document.getElementById("ai-helper-input");
      this.sendBtnEl = document.getElementById("ai-helper-send-btn");
      this.stopBtnEl = document.getElementById("ai-helper-stop-btn");
      this.modelPickerEl = document.getElementById("ai-helper-model-picker");
      this.contextBarEl = document.getElementById("ai-helper-context-bar");
      this.contextTextEl = document.getElementById("ai-helper-context-text");
      this.statusEl = document.getElementById("ai-helper-status-model");

      // Auto-grow textarea
      this.inputEl.addEventListener("input", function () {
        this.style.height = "auto";
        this.style.height = Math.min(this.scrollHeight, 150) + "px";
      });

      // Wire drawer internal events
      document.getElementById("ai-helper-close-btn").addEventListener("click", function () {
        AiHelper.close();
      });

      document.getElementById("ai-helper-clear-btn").addEventListener("click", function () {
        AiHelper.clearSession();
      });

      document.getElementById("ai-helper-clear-context").addEventListener("click", function () {
        AiHelper.setContext(null);
      });

      this.modelPickerEl.addEventListener("change", function () {
        var val = this.value || "";
        if (val && val.indexOf(":") !== -1) {
          var parts = val.split(":");
          AiHelper.selectedProviderId = parts[0];
          AiHelper.selectedModelName = parts.slice(1).join(":");
          localStorage.setItem("ai_helper_selected_target", val);
        } else {
          AiHelper.selectedProviderId = null;
          AiHelper.selectedModelName = null;
          localStorage.removeItem("ai_helper_selected_target");
        }
      });

      document.getElementById("ai-helper-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var msg = AiHelper.inputEl.value.trim();
        if (msg && !AiHelper.isStreaming) {
          AiHelper.send(msg);
        }
      });

      this.stopBtnEl.addEventListener("click", function () {
        AiHelper.stop();
      });

      this.inputEl.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          var msg = AiHelper.inputEl.value.trim();
          if (msg && !AiHelper.isStreaming) {
            AiHelper.send(msg);
          }
        }
      });

      // Delegate suggested prompts
      this.messagesEl.addEventListener("click", function (e) {
        var suggestBtn = e.target.closest("[data-ai-suggest]");
        if (suggestBtn) {
          var prompt = suggestBtn.getAttribute("data-ai-suggest");
          AiHelper.send(prompt);
        }
      });

      // Delegate copy buttons and action badges
      this.drawerEl.addEventListener("click", function (e) {
        var copyCodeBtn = e.target.closest(".ai-code-copy-btn");
        if (copyCodeBtn) {
          var pre = copyCodeBtn.parentElement.querySelector("pre");
          if (pre) {
            navigator.clipboard.writeText(pre.innerText).then(function () {
              var orig = copyCodeBtn.innerText;
              copyCodeBtn.innerText = "Copied";
              setTimeout(function () {
                copyCodeBtn.innerText = orig;
              }, 2000);
            });
          }
          return;
        }

        var actionTag = e.target.closest(".ai-action-tag");
        if (actionTag) {
          var copyVal = actionTag.getAttribute("data-copy");
          if (copyVal) {
            navigator.clipboard.writeText(copyVal).then(function () {
              var prevBg = actionTag.style.background;
              actionTag.style.background = "rgba(16, 185, 129, 0.35)";
              setTimeout(function () {
                actionTag.style.background = prevBg;
              }, 1500);
            });
          }
        }
      });
    },

    _loadProviders: function () {
      var self = this;
      fetch("/plugins/ai_helper/api/providers")
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data.status === "ok" && data.providers && data.providers.length > 0) {
            self.modelPickerEl.innerHTML = "";
            var found = false;
            var targetVal = (self.selectedProviderId && self.selectedModelName)
              ? self.selectedProviderId + ":" + self.selectedModelName
              : null;

            data.providers.forEach(function (p) {
              var models = (p.models && p.models.length > 0) ? p.models : [p.model_name];
              var optGroup = document.createElement("optgroup");
              optGroup.label = p.name + (p.is_default ? " (Default)" : "");

              models.forEach(function (m) {
                var opt = document.createElement("option");
                var optVal = p.id + ":" + m;
                opt.value = optVal;
                opt.textContent = m;

                if (targetVal && targetVal === optVal) {
                  opt.selected = true;
                  found = true;
                } else if (!targetVal && p.is_default && m === p.model_name) {
                  opt.selected = true;
                  found = true;
                  self.selectedProviderId = p.id;
                  self.selectedModelName = m;
                }
                optGroup.appendChild(opt);
              });

              self.modelPickerEl.appendChild(optGroup);
            });

            if (!found && self.modelPickerEl.options.length > 0) {
              self.modelPickerEl.selectedIndex = 0;
              var firstVal = self.modelPickerEl.options[0].value;
              if (firstVal && firstVal.indexOf(":") !== -1) {
                var pParts = firstVal.split(":");
                self.selectedProviderId = pParts[0];
                self.selectedModelName = pParts.slice(1).join(":");
              }
            }
          }
        })
        .catch(function () {});
    },

    _bindGlobalTriggers: function () {
      document.addEventListener("click", function (e) {
        var promptTrigger = e.target.closest("[data-ai-prompt]");
        if (promptTrigger) {
          e.preventDefault();
          var prompt = promptTrigger.getAttribute("data-ai-prompt");
          var ctx = promptTrigger.getAttribute("data-ai-context") || null;
          AiHelper.open({ context: ctx, initialPrompt: prompt });
          return;
        }

        var errorTrigger = e.target.closest("[data-ai-explain-error]");
        if (errorTrigger) {
          e.preventDefault();
          var targetSelector = errorTrigger.getAttribute("data-ai-explain-error");
          var targetEl = document.querySelector(targetSelector);
          var errorText = targetEl ? targetEl.innerText : "Error log unavailable.";
          var errorCtx = errorTrigger.getAttribute("data-ai-context") || "Error Diagnostic";
          AiHelper.explainError(errorText, { context: errorCtx });
          return;
        }
      });
    },

    open: function (options) {
      options = options || {};
      this.init();

      if (options.context) {
        this.setContext(options.context);
      }

      this.drawerEl.classList.add("open");
      this.backdropEl.classList.add("active");
      this.inputEl.focus();

      if (options.initialPrompt) {
        this.send(options.initialPrompt);
      }
    },

    close: function () {
      if (this.drawerEl) this.drawerEl.classList.remove("open");
      if (this.backdropEl) this.backdropEl.classList.remove("active");
    },

    toggle: function () {
      if (this.drawerEl && this.drawerEl.classList.contains("open")) {
        this.close();
      } else {
        this.open();
      }
    },

    setContext: function (context) {
      this.activeContext = context;
      if (this.contextBarEl && this.contextTextEl) {
        if (context) {
          this.contextTextEl.textContent = "Context: " + context.slice(0, 80);
          this.contextBarEl.style.display = "flex";
        } else {
          this.contextBarEl.style.display = "none";
        }
      }
    },

    clearSession: function () {
      if (confirm("Clear AI conversation history?")) {
        var oldSess = this.sessionId;
        this.sessionId = "sess_" + Math.random().toString(36).substring(2, 12);
        localStorage.setItem("ai_helper_session_id", this.sessionId);
        this.messagesEl.innerHTML =
          '<div class="ai-empty-state"><h4>Conversation cleared</h4><p>How can I assist you now?</p></div>';

        fetch("/plugins/ai_helper/api/sessions/" + oldSess, { method: "DELETE" }).catch(
          function () {}
        );
      }
    },

    explainError: function (errorText, options) {
      options = options || {};
      var prompt = "Here is an error log from my server/application. Please explain what caused it and give me the exact step-by-step fix:\n\n```\n" + errorText.trim().slice(-4000) + "\n```";
      this.open({
        context: options.context || "Error Analysis",
        initialPrompt: prompt,
      });
    },

    stop: function () {
      if (this.isStreaming && this.abortController) {
        this.abortController.abort();
        this.isStreaming = false;
        this.sendBtnEl.style.display = "flex";
        this.stopBtnEl.style.display = "none";

        var cursors = this.messagesEl.querySelectorAll(".ai-cursor");
        cursors.forEach(function (c) { c.remove(); });

        if (this.statusEl) {
          this.statusEl.textContent = "Stopped";
        }
      }
    },

    send: function (messageText) {
      if (!messageText || this.isStreaming) return;

      var emptyState = document.getElementById("ai-empty-state");
      if (emptyState) emptyState.remove();

      // 1. Render User Message
      this._appendMessage("user", messageText);
      this.inputEl.value = "";
      this.inputEl.style.height = "auto";

      // 2. Prepare Assistant Bubble
      var assistantBubble = this._appendMessage("assistant", "");
      var bubbleContent = assistantBubble.querySelector(".ai-msg-bubble");
      bubbleContent.innerHTML = '<span class="ai-cursor"></span>';

      this.isStreaming = true;
      this.sendBtnEl.style.display = "none";
      this.stopBtnEl.style.display = "inline-flex";
      if (this.statusEl) {
        this.statusEl.textContent = "Thinking...";
      }

      var fullText = "";
      var csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";
      var startTime = Date.now();

      this.abortController = new AbortController();

      var payload = {
        message: messageText,
        session_id: this.sessionId,
        context: this.activeContext,
        provider_id: this.selectedProviderId ? parseInt(this.selectedProviderId, 10) : undefined,
        model_name: this.selectedModelName || undefined,
        stream: true,
      };

      fetch("/plugins/ai_helper/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify(payload),
        signal: this.abortController.signal,
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("HTTP error " + response.status);
          }
          var reader = response.body.getReader();
          var decoder = new TextDecoder("utf-8");
          var buffer = "";

          function readStream() {
            return reader.read().then(function (result) {
              if (result.done) {
                AiHelper.isStreaming = false;
                AiHelper.sendBtnEl.style.display = "flex";
                AiHelper.stopBtnEl.style.display = "none";
                bubbleContent.innerHTML = AiHelper.renderMarkdown(fullText);
                var duration = Date.now() - startTime;
                if (AiHelper.statusEl) {
                  AiHelper.statusEl.textContent = duration + "ms";
                }
                return;
              }

              buffer += decoder.decode(result.value, { stream: true });
              var lines = buffer.split("\n");
              buffer = lines.pop();

              for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (!line || !line.startsWith("data:")) continue;
                var dataStr = line.substring(5).trim();
                if (dataStr === "[DONE]") {
                  AiHelper.isStreaming = false;
                  AiHelper.sendBtnEl.style.display = "flex";
                  AiHelper.stopBtnEl.style.display = "none";
                  bubbleContent.innerHTML = AiHelper.renderMarkdown(fullText);
                  var dur = Date.now() - startTime;
                  if (AiHelper.statusEl) {
                    AiHelper.statusEl.textContent = dur + "ms";
                  }
                  return;
                }

                try {
                  var parsed = JSON.parse(dataStr);
                  if (parsed.type === "token" && parsed.token) {
                    fullText += parsed.token;
                    bubbleContent.innerHTML =
                      AiHelper.renderMarkdown(fullText) + '<span class="ai-cursor"></span>';
                    AiHelper._scrollToBottom();
                  }
                } catch (e) {}
              }

              return readStream();
            });
          }

          return readStream();
        })
        .catch(function (err) {
          if (err.name === "AbortError") return;
          AiHelper.isStreaming = false;
          AiHelper.sendBtnEl.style.display = "flex";
          AiHelper.stopBtnEl.style.display = "none";
          bubbleContent.innerHTML =
            '<p style="color: var(--color-danger, #ef4444); margin: 0;">Error communicating with AI assistant: ' +
            err.message +
            "</p>";
          if (AiHelper.statusEl) {
            AiHelper.statusEl.textContent = "Error";
          }
        });
    },

    _appendMessage: function (role, content) {
      var wrapper = document.createElement("div");
      wrapper.className = "ai-msg ai-msg--" + role;

      var bubble = document.createElement("div");
      bubble.className = "ai-msg-bubble";
      bubble.innerHTML = this.renderMarkdown(content);
      wrapper.appendChild(bubble);

      var timeEl = document.createElement("span");
      timeEl.className = "ai-msg-time";
      var now = new Date();
      timeEl.textContent = now.getHours() + ":" + (now.getMinutes() < 10 ? "0" : "") + now.getMinutes();
      wrapper.appendChild(timeEl);

      this.messagesEl.appendChild(wrapper);
      this._scrollToBottom();
      return wrapper;
    },

    _scrollToBottom: function () {
      if (this.messagesEl) {
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
      }
    },

    renderMarkdown: function (text) {
      if (!text) return "";
      var escaped = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

      // Replace structured Action Tags: [ACTION:TYPE:VALUE]
      escaped = escaped.replace(
        /\[ACTION:([A-Z_]+):([^\]]+)\]/g,
        function (_, actionType, actionVal) {
          var label = actionType.replace(/_/g, " ").toLowerCase();
          return (
            '<span class="ai-action-tag" data-action="' +
            actionType +
            '" data-copy="' +
            actionVal +
            '" title="Click to copy ' +
            label +
            '">' +
            actionVal +
            "</span>"
          );
        }
      );

      // Code blocks ```lang ... ```
      escaped = escaped.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, function (_, lang, code) {
        return (
          '<div class="ai-code-block">' +
          '<button type="button" class="ai-code-copy-btn">Copy</button>' +
          "<pre><code>" + code.trim() + "</code></pre>" +
          "</div>"
        );
      });

      // Inline code `code`
      escaped = escaped.replace(/`([^`]+)`/g, "<code>$1</code>");

      // Bold **text**
      escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

      // Italic *text*
      escaped = escaped.replace(/\*([^*]+)\*/g, "<em>$1</em>");

      // Convert newlines to paragraphs/breaks
      var paragraphs = escaped.split(/\n\n+/);
      return paragraphs
        .map(function (p) {
          return "<p>" + p.replace(/\n/g, "<br>") + "</p>";
        })
        .join("");
    },
  };

  // Expose globally
  window.AiHelper = AiHelper;

  // Auto-init once DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      AiHelper.init();
    });
  } else {
    AiHelper.init();
  }
})();
