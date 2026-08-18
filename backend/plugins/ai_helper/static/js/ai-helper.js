/**
 * ai-helper.js — Universal Vanilla JS Client SDK for SRV / Barq AI Assistant.
 * Multi-session conversation history, task separation, instant local caching,
 * dynamic model switcher, live SSE streaming, and rich Markdown formatting.
 */
(function () {
  "use strict";

  var TASK_META = {
    general: { label: "General" },
    error_diag: { label: "Error Diagnostic" },
    domain: { label: "Domains & SSL" },
    app: { label: "Apps & Docker" },
    container: { label: "Apps & Docker" },
    database: { label: "Database" },
    file_manager: { label: "Files & Code" },
    system: { label: "System & VPS" },
  };

  var AiHelper = {
    TASK_META: TASK_META,
    sessionId: null,
    sessionTitle: "New Chat",
    activeTaskType: "general",
    activeContext: null,
    selectedProviderId: null,
    selectedModelName: null,
    isStreaming: false,
    abortController: null,

    // DOM Elements
    drawerEl: null,
    backdropEl: null,
    messagesEl: null,
    inputEl: null,
    sendBtnEl: null,
    stopBtnEl: null,
    contextBarEl: null,
    contextTextEl: null,
    statusEl: null,
    taskBadgeEl: null,

    init: function () {
      if (document.getElementById("ai-helper-drawer")) return;

      var cache = window.AiHelperCache;
      this.sessionId = cache ? cache.getActiveSessionId() : null;
      if (!this.sessionId) {
        this.sessionId = "sess_" + Math.random().toString(36).substring(2, 12);
        if (cache) cache.setActiveSessionId(this.sessionId);
      }

      this._injectDOM();
      this._initComponents();
      this._wireEvents();
      this._bindGlobalTriggers();
      this._renderActiveSessionHistory();
    },

    _injectDOM: function () {
      var self = this;

      // 1. Floating Launcher Button
      var floatBtn = document.createElement("button");
      floatBtn.id = "ai-helper-floating-btn";
      floatBtn.className = "ai-helper-floating-btn";
      floatBtn.type = "button";
      floatBtn.setAttribute("aria-label", "Open AI Assistant");
      floatBtn.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg>';
      floatBtn.addEventListener("click", function () { self.toggle(); });
      document.body.appendChild(floatBtn);

      // 2. Backdrop
      var backdrop = document.createElement("div");
      backdrop.id = "ai-helper-backdrop";
      backdrop.className = "ai-helper-backdrop";
      backdrop.addEventListener("click", function () { self.close(); });
      document.body.appendChild(backdrop);
      this.backdropEl = backdrop;

      // 3. Drawer Shell
      var drawer = document.createElement("aside");
      drawer.id = "ai-helper-drawer";
      drawer.className = "ai-helper-drawer";
      drawer.innerHTML = [
        '<div class="ai-helper-header">',
        '  <div class="ai-helper-header-info">',
        '    <span class="ai-helper-header-icon"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg></span>',
        '    <h3 class="ai-helper-title">AI Assistant</h3>',
        '    <span class="ai-helper-task-badge" id="ai-helper-task-badge" title="Active Task Scope">General</span>',
        "  </div>",
        '  <div class="ai-helper-header-actions">',
        '    <button type="button" class="ai-helper-btn-icon" id="ai-helper-new-chat-btn" title="Start New Conversation (+)"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg></button>',
        '    <button type="button" class="ai-helper-btn-icon" id="ai-helper-history-toggle-btn" title="Conversations & Tasks"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg></button>',
        '    <button type="button" class="ai-helper-btn-icon" id="ai-helper-close-btn" title="Close"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg></button>',
        "  </div>",
        "</div>",
        '<div class="ai-helper-history-panel" id="ai-helper-history-panel">',
        '  <div class="ai-helper-history-header">',
        '    <div class="ai-helper-history-title-row"><span class="ai-helper-history-title">Conversations & Tasks</span><button type="button" class="btn btn--secondary btn--sm" id="ai-helper-history-back-btn" style="height: 24px; padding: 0 8px; font-size: 11px;">← Back to Chat</button></div>',
        '    <div class="ai-helper-history-search-box"><input type="text" id="ai-helper-history-search" class="ai-helper-history-search-input" placeholder="Search conversations..."></div>',
        '    <div class="ai-helper-task-chips" id="ai-helper-task-chips"><button type="button" class="ai-task-chip active" data-task-filter="all">All</button><button type="button" class="ai-task-chip" data-task-filter="general">General</button><button type="button" class="ai-task-chip" data-task-filter="error_diag">Errors</button><button type="button" class="ai-task-chip" data-task-filter="app">Apps</button><button type="button" class="ai-task-chip" data-task-filter="domain">Domains</button><button type="button" class="ai-task-chip" data-task-filter="database">Databases</button></div>',
        "  </div>",
        '  <div class="ai-helper-history-list" id="ai-helper-history-list"><div class="ai-history-loading">Loading conversations...</div></div>',
        '  <div class="ai-helper-history-footer"><button type="button" class="ai-helper-history-clear-all" id="ai-helper-clear-all-btn">Clear All History</button></div>',
        "</div>",
        '<div class="ai-helper-chat-body" id="ai-helper-chat-body">',
        '  <div class="ai-helper-context-bar" id="ai-helper-context-bar" style="display: none;"><span class="ai-helper-context-text" id="ai-helper-context-text"></span><button type="button" class="ai-helper-btn-icon" style="width:20px;height:20px;" id="ai-helper-clear-context" title="Clear context">✕</button></div>',
        '  <div class="ai-helper-messages" id="ai-helper-messages"></div>',
        "</div>",
        '<div class="ai-helper-model-modal" id="ai-helper-model-modal">',
        '  <div class="ai-helper-model-modal-backdrop" id="ai-helper-model-modal-backdrop"></div>',
        '  <div class="ai-helper-model-modal-content"><button type="button" class="ai-helper-model-arrow-btn" id="ai-helper-model-arrow-up" title="Previous model"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="18 15 12 9 6 15"></polyline></svg></button><div class="ai-helper-model-viewport" id="ai-helper-model-viewport"><div class="ai-helper-model-list" id="ai-helper-model-list"></div></div><button type="button" class="ai-helper-model-arrow-btn" id="ai-helper-model-arrow-down" title="Next model"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"></polyline></svg></button></div>',
        "</div>",
        '<div class="ai-helper-footer" id="ai-helper-footer">',
        '  <form class="ai-helper-input-box" id="ai-helper-form">',
        '    <textarea class="ai-helper-textarea" id="ai-helper-input" rows="2" placeholder="Ask a question, run task, or paste error logs..."></textarea>',
        '    <div class="ai-helper-toolbar">',
        '      <div class="ai-helper-toolbar-left"><button type="button" class="ai-helper-model-trigger" id="ai-helper-model-trigger" title="Switch AI Model"><span class="ai-helper-model-trigger-name" id="ai-helper-model-trigger-text">Select Model</span><span class="ai-helper-model-trigger-chevron">▾</span></button><span class="ai-helper-status-pill" id="ai-helper-status-model">Ready</span></div>',
        '      <div class="ai-helper-toolbar-right"><button type="button" class="btn btn--danger btn--sm" id="ai-helper-stop-btn" style="display: none; height: 26px; padding: 0 8px; font-size: 11px; min-width: auto;" title="Stop generation"><svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg> Stop</button><button type="submit" class="btn btn--primary btn--sm" id="ai-helper-send-btn" style="width: 26px; height: 26px; padding: 0; min-width: 26px;" title="Send message (Enter)"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg></button></div>',
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
      this.contextBarEl = document.getElementById("ai-helper-context-bar");
      this.contextTextEl = document.getElementById("ai-helper-context-text");
      this.statusEl = document.getElementById("ai-helper-status-model");
      this.taskBadgeEl = document.getElementById("ai-helper-task-badge");
    },

    _initComponents: function () {
      var self = this;

      // 1. History Module
      if (window.AiHelperHistory) {
        window.AiHelperHistory.init(
          document.getElementById("ai-helper-history-panel"),
          document.getElementById("ai-helper-history-list"),
          document.getElementById("ai-helper-history-search"),
          function (sessId) { self.switchSession(sessId); },
          function (sessId) { self.deleteSession(sessId); },
          function () { self.clearAllHistory(); }
        );
      }

      // 2. Model Switcher Module
      if (window.AiHelperModels) {
        window.AiHelperModels.init("ai-helper-model-trigger", "ai-helper-model-modal", function (pId, mName) {
          self.selectedProviderId = pId;
          self.selectedModelName = mName;
        });
      }

      // 3. Interactive Actions & Tools Module
      if (window.AiHelperActions) {
        window.AiHelperActions.init(this.messagesEl);
      }

      // 4. Code View Window Module
      if (window.AiHelperCodeView) {
        window.AiHelperCodeView.init();
      }

      // 5. Shortcut Mentions (@) and Slash Commands (/) Module
      if (window.AiHelperMentions) {
        window.AiHelperMentions.init(this.inputEl, document.getElementById("ai-helper-footer"));
      }

      // 6. Resize Controller Module
      if (window.AiHelperResize) {
        window.AiHelperResize.init(this.drawerEl);
      }
    },

    _wireEvents: function () {
      var self = this;
      this.inputEl.addEventListener("input", function () {
        this.style.height = "auto";
        this.style.height = Math.min(this.scrollHeight, 150) + "px";
      });

      document.getElementById("ai-helper-close-btn").addEventListener("click", function () { self.close(); });
      document.getElementById("ai-helper-new-chat-btn").addEventListener("click", function () { self.startNewChat({ taskType: "general" }); });
      document.getElementById("ai-helper-history-toggle-btn").addEventListener("click", function () { self.toggleHistoryView(); });
      document.getElementById("ai-helper-history-back-btn").addEventListener("click", function () { self.closeHistoryView(); });
      document.getElementById("ai-helper-clear-all-btn").addEventListener("click", function () { self.clearAllHistory(); });
      document.getElementById("ai-helper-clear-context").addEventListener("click", function () { self.setContext(null); });

      document.getElementById("ai-helper-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var msg = self.inputEl.value.trim();
        if (msg && !self.isStreaming) self.send(msg);
      });
      this.stopBtnEl.addEventListener("click", function () { self.stop(); });
      this.inputEl.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          var msg = self.inputEl.value.trim();
          if (msg && !self.isStreaming) self.send(msg);
        }
      });

      this.messagesEl.addEventListener("click", function (e) {
        var suggest = e.target.closest("[data-ai-suggest]");
        if (suggest) self.send(suggest.getAttribute("data-ai-suggest"));
      });
    },

    _getCsrfToken: function () {
      var meta = document.querySelector('meta[name="csrf-token"]');
      if (meta && meta.getAttribute("content")) return meta.getAttribute("content");
      var input = document.querySelector('input[name="csrf_token"]');
      if (input && input.value) return input.value;
      return "";
    },

    _updateTaskBadge: function () {
      if (!this.taskBadgeEl) return;
      var meta = TASK_META[this.activeTaskType] || TASK_META.general;
      this.taskBadgeEl.textContent = meta.label;
      if (this.activeTaskType && this.activeTaskType !== "general") {
        this.taskBadgeEl.classList.add("ai-helper-task-badge--active");
      } else {
        this.taskBadgeEl.classList.remove("ai-helper-task-badge--active");
      }
    },

    setTaskType: function (t) { this.activeTaskType = t || "general"; this._updateTaskBadge(); },
    setContext: function (ctx) {
      this.activeContext = ctx;
      if (this.contextBarEl && this.contextTextEl) {
        this.contextTextEl.textContent = ctx ? "Context: " + ctx.slice(0, 80) : "";
        this.contextBarEl.style.display = ctx ? "flex" : "none";
      }
    },

    _renderActiveSessionHistory: function () {
      var cache = window.AiHelperCache;
      var messages = cache ? cache.getCachedMessages(this.sessionId) : [];
      this.messagesEl.innerHTML = "";
      if (messages && messages.length > 0) {
        for (var i = 0; i < messages.length; i++) {
          this._appendMessageToDOM(messages[i].role, messages[i].content, messages[i].created_at);
        }
        this._scrollToBottom();
      } else {
        this._renderEmptyState();
      }
      this._syncSessionMessagesFromServer(this.sessionId);
    },

    _renderEmptyState: function () {
      var meta = TASK_META[this.activeTaskType] || TASK_META.general;
      var title = this.activeTaskType === "general" ? "How can I help you today?" : "How can I help with " + meta.label + "?";

      var suggestions = [
        { label: "Deploy a Node.js / Python app", prompt: "How do I deploy an application using Docker or PM2 on this panel?" },
        { label: "Fix 502 Bad Gateway error", prompt: "What are the common causes and step-by-step fix for 502 Bad Gateway?" },
        { label: "Nginx reverse proxy setup", prompt: "How do I configure Nginx reverse proxy routing and SSL?" },
      ];

      if (this.activeTaskType === "error_diag") {
        suggestions = [
          { label: "Explain error logs", prompt: "Please explain what caused this error and how to fix it:\n\n```\n\n```" },
          { label: "Troubleshoot 502/504 Gateway error", prompt: "How do I diagnose 502/504 errors on Nginx and upstream services?" },
          { label: "Check high CPU or memory", prompt: "How do I identify and fix processes causing high CPU or memory?" },
        ];
      } else if (this.activeTaskType === "domain") {
        suggestions = [
          { label: "Setup Nginx reverse proxy", prompt: "How do I configure Nginx reverse proxy for a custom domain?" },
          { label: "Fix Let's Encrypt SSL error", prompt: "How do I troubleshoot and renew Let's Encrypt SSL certificate?" },
          { label: "Add security headers", prompt: "What security headers should I add to my Nginx configuration?" },
        ];
      } else if (this.activeTaskType === "app" || this.activeTaskType === "container") {
        suggestions = [
          { label: "Write a Dockerfile", prompt: "Can you help me write an optimized Dockerfile for my app?" },
          { label: "Docker Compose setup", prompt: "How do I configure docker-compose for multi-container apps?" },
          { label: "Check container logs", prompt: "How do I view and troubleshoot live Docker container logs?" },
        ];
      } else if (this.activeTaskType === "database") {
        suggestions = [
          { label: "PostgreSQL connection test", prompt: "How do I test and troubleshoot PostgreSQL connection and credentials?" },
          { label: "Optimize database performance", prompt: "What are best practices for database indexes and memory settings?" },
          { label: "Database backup & restore", prompt: "How do I backup and restore databases safely?" },
        ];
      } else if (this.activeTaskType === "system") {
        suggestions = [
          { label: "Check disk space & clean cache", prompt: "How do I check VPS disk usage and clean unnecessary cache files?" },
          { label: "Inspect system memory & swap", prompt: "How do I configure swap and check RAM usage on this VPS?" },
          { label: "Firewall & open ports", prompt: "How do I verify open network ports and firewall rules?" },
        ];
      }

      var itemsHtml = suggestions
        .map(function (s) {
          return (
            '<button type="button" class="ai-suggested-item" data-ai-suggest="' +
            s.prompt.replace(/"/g, "&quot;") +
            '"><span>' +
            s.label +
            '</span><span class="ai-suggested-arrow">→</span></button>'
          );
        })
        .join("\n");

      this.messagesEl.innerHTML = [
        '<div class="ai-empty-state" id="ai-empty-state">',
        '  <div class="ai-empty-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg></div>',
        '  <h4 class="ai-empty-title">' + title + "</h4>",
        '  <div class="ai-empty-hint">Ask a question or select a prompt:</div>',
        '  <div class="ai-suggested-prompts">',
        itemsHtml,
        "  </div>",
        "</div>",
      ].join("\n");
    },

    _syncSessionMessagesFromServer: function (sessionId) {
      var self = this;
      fetch("/plugins/ai_helper/api/sessions/" + sessionId + "/messages")
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (data) {
          if (data && data.status === "ok" && Array.isArray(data.messages) && data.session_id === self.sessionId) {
            var cache = window.AiHelperCache;
            if (cache) cache.setSession(sessionId, { title: self.sessionTitle, task_type: self.activeTaskType }, data.messages);
            self.messagesEl.innerHTML = "";
            if (data.messages.length > 0) {
              data.messages.forEach(function (m) { self._appendMessageToDOM(m.role, m.content, m.created_at); });
              self._scrollToBottom();
            } else {
              self._renderEmptyState();
            }
          }
        }).catch(function () {});
    },

    startNewChat: function (opts) {
      opts = opts || {};
      this.sessionId = "sess_" + Math.random().toString(36).substring(2, 12);
      this.sessionTitle = opts.title || "New Chat";
      this.activeTaskType = opts.taskType || "general";
      this.setContext(opts.context || null);

      var cache = window.AiHelperCache;
      if (cache) cache.setActiveSessionId(this.sessionId);

      this._updateTaskBadge();
      this.closeHistoryView();
      this.messagesEl.innerHTML = "";
      this._renderEmptyState();
      this.inputEl.value = "";
      this.inputEl.focus();
      if (opts.initialPrompt) this.send(opts.initialPrompt);
    },

    switchSession: function (sessId) {
      if (!sessId) return;
      this.sessionId = sessId;
      var cache = window.AiHelperCache;
      if (cache) cache.setActiveSessionId(sessId);
      this.closeHistoryView();
      this._renderActiveSessionHistory();
      this.inputEl.focus();
    },

    deleteSession: function (sessId) {
      if (!confirm("Delete this conversation?")) return;
      var cache = window.AiHelperCache;
      if (cache) cache.removeSession(sessId);
      if (window.AiHelperHistory) window.AiHelperHistory.removeSession(sessId);
      var csrf = this._getCsrfToken();
      fetch("/plugins/ai_helper/api/sessions/" + sessId, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf },
      }).catch(function () {});
      if (this.sessionId === sessId) this.startNewChat({ taskType: "general" });
    },

    clearAllHistory: function () {
      if (!confirm("Clear ALL conversation histories? This cannot be undone.")) return;
      var cache = window.AiHelperCache;
      if (cache) cache.clearAll();
      if (window.AiHelperHistory) window.AiHelperHistory.clearAll();
      var csrf = this._getCsrfToken();
      fetch("/plugins/ai_helper/api/sessions", {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf },
      }).catch(function () {});
      this.startNewChat({ taskType: "general" });
    },

    toggleHistoryView: function () {
      if (window.AiHelperHistory && typeof window.AiHelperHistory.isOpen === "function") {
        if (window.AiHelperHistory.isOpen()) this.closeHistoryView();
        else this.openHistoryView();
      } else {
        var panel = document.getElementById("ai-helper-history-panel");
        if (panel && panel.classList.contains("open")) this.closeHistoryView();
        else this.openHistoryView();
      }
    },
    openHistoryView: function () {
      if (window.AiHelperHistory) window.AiHelperHistory.open();
      else {
        var panel = document.getElementById("ai-helper-history-panel");
        if (panel) panel.classList.add("open");
      }
    },
    closeHistoryView: function () {
      if (window.AiHelperHistory) window.AiHelperHistory.close();
      else {
        var panel = document.getElementById("ai-helper-history-panel");
        if (panel) panel.classList.remove("open");
      }
    },

    send: function (msg) {
      if (!msg || this.isStreaming) return;
      var empty = document.getElementById("ai-empty-state");
      if (empty) empty.remove();

      this._appendMessageToDOM("user", msg);
      var cache = window.AiHelperCache;
      if (cache) cache.appendMessage(this.sessionId, { role: "user", content: msg, created_at: new Date().toISOString() }, { title: this.sessionTitle, taskType: this.activeTaskType, context: this.activeContext });

      this.inputEl.value = "";
      this.inputEl.style.height = "auto";

      var assistantBubble = this._appendMessageToDOM("assistant", "");
      var bubbleContent = assistantBubble.querySelector(".ai-msg-bubble");
      bubbleContent.innerHTML = '<span class="ai-cursor"></span>';

      this.isStreaming = true;
      this.sendBtnEl.style.display = "none";
      this.stopBtnEl.style.display = "inline-flex";
      if (this.statusEl) this.statusEl.textContent = "Thinking...";

      var fullText = "";
      var startTime = Date.now();
      var self = this;
      this.abortController = new AbortController();

      fetch("/plugins/ai_helper/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": this._getCsrfToken() },
        body: JSON.stringify({ message: msg, session_id: this.sessionId, task_type: this.activeTaskType, session_title: this.sessionTitle, context_key: this.activeContext, context: this.activeContext, provider_id: this.selectedProviderId ? parseInt(this.selectedProviderId, 10) : undefined, model_name: this.selectedModelName || undefined, stream: true }),
        signal: this.abortController.signal,
      }).then(function (res) {
        if (!res.ok) throw new Error("HTTP error " + res.status);
        var reader = res.body.getReader();
        var decoder = new TextDecoder("utf-8");
        var buffer = "";
        function read() {
          return reader.read().then(function (result) {
            if (result.done) return self._finishStreaming(bubbleContent, fullText, startTime);
            buffer += decoder.decode(result.value, { stream: true });
            var lines = buffer.split("\n");
            buffer = lines.pop();
            for (var i = 0; i < lines.length; i++) {
              var line = lines[i].trim();
              if (!line || !line.startsWith("data:")) continue;
              var d = line.substring(5).trim();
              if (d === "[DONE]") return self._finishStreaming(bubbleContent, fullText, startTime);
              try {
                var p = JSON.parse(d);
                if (p.type === "token" && p.token) {
                  fullText += p.token;
                  bubbleContent.innerHTML = self.renderMarkdown(fullText) + '<span class="ai-cursor"></span>';
                  self._scrollToBottom();
                }
              } catch (e) {}
            }
            return read();
          });
        }
        return read();
      }).catch(function (err) {
        if (err.name === "AbortError") return;
        self.isStreaming = false;
        self.sendBtnEl.style.display = "flex";
        self.stopBtnEl.style.display = "none";
        bubbleContent.innerHTML = '<p style="color: var(--color-danger, #ef4444); margin: 0;">Error: ' + err.message + "</p>";
        if (self.statusEl) self.statusEl.textContent = "Error";
      });
    },

    _finishStreaming: function (bubble, text, start) {
      this.isStreaming = false;
      this.sendBtnEl.style.display = "flex";
      this.stopBtnEl.style.display = "none";
      bubble.innerHTML = this.renderMarkdown(text);
      if (this.statusEl) this.statusEl.textContent = (Date.now() - start) + "ms";
      if (text.trim() && window.AiHelperCache) {
        window.AiHelperCache.appendMessage(this.sessionId, { role: "assistant", content: text, created_at: new Date().toISOString() }, { title: this.sessionTitle, taskType: this.activeTaskType, context: this.activeContext });
      }
      if (window.AiHelperActions) window.AiHelperActions.checkLongMessages(this.messagesEl);
    },

    _appendMessageToDOM: function (role, content, timeIso) {
      var wrap = document.createElement("div");
      wrap.className = "ai-msg ai-msg--" + role;
      var bubble = document.createElement("div");
      bubble.className = "ai-msg-bubble";
      bubble.innerHTML = this.renderMarkdown(content);
      wrap.appendChild(bubble);
      var time = document.createElement("span");
      time.className = "ai-msg-time";
      var d = timeIso ? new Date(timeIso) : new Date();
      time.textContent = d.getHours() + ":" + (d.getMinutes() < 10 ? "0" : "") + d.getMinutes();
      wrap.appendChild(time);
      this.messagesEl.appendChild(wrap);
      if (window.AiHelperActions) window.AiHelperActions.checkLongMessages(this.messagesEl);
      this._scrollToBottom();
      return wrap;
    },

    _scrollToBottom: function () {
      if (this.messagesEl) this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    },

    renderMarkdown: function (text) {
      return window.AiHelperMarkdown ? window.AiHelperMarkdown.render(text) : (text || "");
    },

    _bindGlobalTriggers: function () {
      var self = this;
      document.addEventListener("click", function (e) {
        var p = e.target.closest("[data-ai-prompt]");
        if (p) {
          e.preventDefault();
          self.open({ context: p.getAttribute("data-ai-context"), initialPrompt: p.getAttribute("data-ai-prompt"), taskType: p.getAttribute("data-ai-task") || "general" });
          return;
        }
        var err = e.target.closest("[data-ai-explain-error]");
        if (err) {
          e.preventDefault();
          var targetEl = document.querySelector(err.getAttribute("data-ai-explain-error"));
          self.explainError(targetEl ? targetEl.innerText : "Error log unavailable.", { context: err.getAttribute("data-ai-context") || "Error Diagnostic" });
        }
      });
    },

    open: function (opts) {
      opts = opts || {};
      this.init();
      if (opts.taskType) this.setTaskType(opts.taskType);
      if (opts.context) this.setContext(opts.context);
      this.drawerEl.classList.add("open");
      this.backdropEl.classList.add("active");
      this.closeHistoryView();
      this.inputEl.focus();
      if (opts.initialPrompt) {
        var msgs = window.AiHelperCache ? window.AiHelperCache.getCachedMessages(this.sessionId) : [];
        if (msgs && msgs.length > 0) this.startNewChat({ taskType: opts.taskType || this.activeTaskType, context: opts.context || this.activeContext, initialPrompt: opts.initialPrompt });
        else this.send(opts.initialPrompt);
      }
    },

    close: function () {
      if (this.drawerEl) this.drawerEl.classList.remove("open");
      if (this.backdropEl) this.backdropEl.classList.remove("active");
      this.closeHistoryView();
    },

    toggle: function () {
      if (this.drawerEl && this.drawerEl.classList.contains("open")) this.close();
      else this.open();
    },

    explainError: function (errText, opts) {
      opts = opts || {};
      this.open({ taskType: "error_diag", context: opts.context || "Error Diagnostic", initialPrompt: "Here is an error log from my server/application. Please explain what caused it and give me the exact step-by-step fix:\n\n```\n" + (errText || "").trim().slice(-4000) + "\n```" });
    },

    stop: function () {
      if (this.isStreaming && this.abortController) {
        this.abortController.abort();
        this.isStreaming = false;
        this.sendBtnEl.style.display = "flex";
        this.stopBtnEl.style.display = "none";
        this.messagesEl.querySelectorAll(".ai-cursor").forEach(function (c) { c.remove(); });
        if (this.statusEl) this.statusEl.textContent = "Stopped";
      }
    },
  };

  window.AiHelper = AiHelper;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { AiHelper.init(); });
  } else {
    AiHelper.init();
  }
})();
