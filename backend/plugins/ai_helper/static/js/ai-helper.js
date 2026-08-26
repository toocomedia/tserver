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
        '  <div class="ai-helper-decision-bar" id="ai-helper-decision-bar" style="display: none;"></div>',
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

      // 7. Quick-Decision Bar Module (Pinned above input)
      if (window.AiHelperDecisionBar) {
        window.AiHelperDecisionBar.init(
          document.getElementById("ai-helper-decision-bar"),
          function (reply) { self.send(reply); }
        );
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
        var lastMsg = messages[messages.length - 1];
        if (lastMsg && lastMsg.role === "assistant" && window.AiHelperDecisionBar) {
          window.AiHelperDecisionBar.extractAndShow(lastMsg.content);
        }
        this._scrollToBottom();
      } else {
        if (window.AiHelperDecisionBar) window.AiHelperDecisionBar.hide();
        this._renderEmptyState();
      }
      this._syncSessionMessagesFromServer(this.sessionId);
    },

    _renderEmptyState: function () {
      var meta = TASK_META[this.activeTaskType] || TASK_META.general;
      var title = this.activeTaskType === "general" ? "How can I help you today?" : "How can I help with " + meta.label + "?";
      var hint = "Select a capability or ask anything about your server:";

      var ICONS = {
        shield: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>',
        globe: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>',
        diag: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>',
        app: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>',
        db: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>',
        file: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>',
        server: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line></svg>',
      };

      var suggestions = [
        {
          icon: "shield",
          label: "Domain Security & SSL Check",
          desc: "Audit domains, SSL certificates & expiry",
          prompt: "Please perform a security check on all domains: verify SSL certificates, expiration dates, reverse proxy routes, and report any misconfigurations or vulnerabilities."
        },
        {
          icon: "diag",
          label: "Diagnose Server & Error Logs",
          desc: "Inspect recent app logs and 502/504 errors",
          prompt: "Please check recent application and container error logs to diagnose any crashes, failed deployments, or performance bottlenecks."
        },
        {
          icon: "app",
          label: "Apps & Container Overview",
          desc: "List running apps, ports & runtime status",
          prompt: "List all deployed applications (PHP, Python, Container/Railpack), their status, ports, and verify that all services are running properly."
        },
        {
          icon: "db",
          label: "Databases & Project Files",
          desc: "Explore database instances & website roots",
          prompt: "Show active database instances (PostgreSQL, MariaDB, SQLite) and list website project directories."
        }
      ];

      if (this.activeTaskType === "error_diag") {
        suggestions = [
          {
            icon: "diag",
            label: "Diagnose App Crash Logs",
            desc: "Analyze recent error logs for root causes",
            prompt: "Please diagnose recent application crash logs and give me step-by-step resolution."
          },
          {
            icon: "server",
            label: "Troubleshoot 502/504 Gateway Error",
            desc: "Check Nginx upstream connection & ports",
            prompt: "How do I diagnose 502 Bad Gateway and 504 Gateway Timeout errors on Nginx and upstream services?"
          },
          {
            icon: "diag",
            label: "Identify CPU & Memory Spikes",
            desc: "Locate processes consuming high VPS resources",
            prompt: "How do I identify and fix processes causing high CPU or memory spikes on this VPS?"
          }
        ];
      } else if (this.activeTaskType === "domain") {
        suggestions = [
          {
            icon: "shield",
            label: "Domain Security & SSL Audit",
            desc: "Audit domain SSL expiration & proxy bindings",
            prompt: "Run a security audit for all registered domains, SSL expiration dates, and reverse proxy bindings."
          },
          {
            icon: "globe",
            label: "Nginx Reverse Proxy Routing",
            desc: "Inspect upstream ports & proxy headers",
            prompt: "How do I configure Nginx reverse proxy routing, WebSocket support, and SSL for a custom domain?"
          },
          {
            icon: "globe",
            label: "Check DNS Records",
            desc: "Inspect PowerDNS zones (A, CNAME, MX)",
            prompt: "Query DNS records and zones configured on this panel."
          }
        ];
      } else if (this.activeTaskType === "app" || this.activeTaskType === "container") {
        suggestions = [
          {
            icon: "app",
            label: "Check Container & App Status",
            desc: "Inspect live runtime status and recent logs",
            prompt: "Check status and recent logs of all installed applications and containers."
          },
          {
            icon: "file",
            label: "Write Optimized Dockerfile",
            desc: "Generate Dockerfile & compose configuration",
            prompt: "Can you help me write an optimized Dockerfile and docker-compose setup for my app?"
          },
          {
            icon: "diag",
            label: "Fix Deployment Failures",
            desc: "Troubleshoot build steps & startup crashes",
            prompt: "Why did my application build or deployment fail? Check recent logs and explain how to fix it."
          }
        ];
      } else if (this.activeTaskType === "database") {
        suggestions = [
          {
            icon: "db",
            label: "List Active Databases",
            desc: "Inspect PostgreSQL, MariaDB & SQLite instances",
            prompt: "Show active database instances (PostgreSQL, MariaDB, SQLite) and verify connection status."
          },
          {
            icon: "shield",
            label: "Database Backup & Safety",
            desc: "Best practices for automated backups & restore",
            prompt: "How do I backup and restore databases safely on this server?"
          },
          {
            icon: "server",
            label: "Optimize Database Performance",
            desc: "Tune buffer pools, connections & indexes",
            prompt: "What are best practices for database indexes and memory settings on a VPS?"
          }
        ];
      } else if (this.activeTaskType === "file_manager") {
        suggestions = [
          {
            icon: "file",
            label: "Explore Website Root Files",
            desc: "List directories & code files in web root",
            prompt: "List files and subdirectories in the website document root."
          },
          {
            icon: "shield",
            label: "Audit Configuration Files",
            desc: "Check nginx.conf, Dockerfile & environment",
            prompt: "Read and audit the web server configuration (nginx.conf, Dockerfile, env settings) for security and best practices."
          },
          {
            icon: "shield",
            label: "Scan for Suspicious Files",
            desc: "Detect malicious scripts or wrong permissions",
            prompt: "Scan website directory for unexpected scripts or permission issues."
          }
        ];
      } else if (this.activeTaskType === "system") {
        suggestions = [
          {
            icon: "server",
            label: "Check Disk Space & Clean Cache",
            desc: "Inspect disk usage & clean build caches",
            prompt: "How do I check VPS disk usage and clean unnecessary cache and log files?"
          },
          {
            icon: "server",
            label: "Inspect Memory & Swap",
            desc: "Review RAM usage & swap configuration",
            prompt: "How do I configure swap and check RAM usage on this VPS?"
          },
          {
            icon: "shield",
            label: "Firewall & Port Audit",
            desc: "Verify open network ports & UFW rules",
            prompt: "How do I verify open network ports and firewall rules on this server?"
          }
        ];
      }

      var itemsHtml = suggestions
        .map(function (s) {
          var iconSvg = ICONS[s.icon] || ICONS.app;
          return (
            '<button type="button" class="ai-suggested-item" data-ai-suggest="' +
            s.prompt.replace(/"/g, "&quot;") +
            '">' +
            '  <span class="ai-suggested-item-left">' +
            '    <span class="ai-suggested-icon">' + iconSvg + '</span>' +
            '    <span class="ai-suggested-info">' +
            '      <span class="ai-suggested-label">' + s.label + '</span>' +
            '      <span class="ai-suggested-desc">' + s.desc + '</span>' +
            '    </span>' +
            '  </span>' +
            '  <span class="ai-suggested-arrow">→</span>' +
            '</button>'
          );
        })
        .join("\n");

      this.messagesEl.innerHTML = [
        '<div class="ai-empty-state" id="ai-empty-state">',
        '  <div class="ai-empty-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg></div>',
        '  <h4 class="ai-empty-title">' + title + "</h4>",
        '  <div class="ai-empty-hint">' + hint + "</div>",
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
              var lastMsg = data.messages[data.messages.length - 1];
              if (lastMsg && lastMsg.role === "assistant" && window.AiHelperDecisionBar) {
                window.AiHelperDecisionBar.extractAndShow(lastMsg.content);
              }
              self._scrollToBottom();
            } else {
              if (window.AiHelperDecisionBar) window.AiHelperDecisionBar.hide();
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
      if (window.AiHelperDecisionBar) window.AiHelperDecisionBar.hide();
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
      if (window.AiHelperDecisionBar) {
        window.AiHelperDecisionBar.hide();
      }
      var cache = window.AiHelperCache;
      if (cache) cache.appendMessage(this.sessionId, { role: "user", content: msg, created_at: new Date().toISOString() }, { title: this.sessionTitle, taskType: this.activeTaskType, context: this.activeContext });

      this.inputEl.value = "";
      this.inputEl.style.height = "auto";

      var assistantWrap = this._appendMessageToDOM("assistant", "");
      var bubbleContent = assistantWrap.querySelector(".ai-msg-bubble");
      bubbleContent.innerHTML = '<span class="ai-cursor"></span>';

      // Activity panel — shows real-time tool reads and grounding evidence
      var activityPanel = document.createElement("div");
      activityPanel.className = "ai-activity-panel";
      assistantWrap.appendChild(activityPanel);
      var activityItems = {}; // tool_name -> item element

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
            if (result.done) return self._finishStreaming(bubbleContent, activityPanel, fullText, startTime, activityItems);
            buffer += decoder.decode(result.value, { stream: true });
            var lines = buffer.split("\n");
            buffer = lines.pop();
            for (var i = 0; i < lines.length; i++) {
              var line = lines[i].trim();
              if (!line || !line.startsWith("data:")) continue;
              var d = line.substring(5).trim();
              if (d === "[DONE]") return self._finishStreaming(bubbleContent, activityPanel, fullText, startTime, activityItems);
              try {
                var p = JSON.parse(d);
                if (p.type === "token" && p.token) {
                  fullText += p.token;
                  bubbleContent.innerHTML = self.renderMarkdown(fullText) + '<span class="ai-cursor"></span>';
                  self._scrollToBottom();
                } else if (p.type === "tool_activity" && p.activity) {
                  self._updateActivityPanel(activityPanel, activityItems, p.activity);
                  if (self.statusEl) {
                    self.statusEl.textContent = p.activity.status === "start"
                      ? (p.activity.icon + " " + p.activity.label + "...")
                      : "Processing...";
                  }
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
        var errorDisplay = err.message || "Connection error";
        if (errorDisplay.toLowerCase().includes("failed to fetch") || errorDisplay.toLowerCase().includes("network error")) {
          errorDisplay = "Connection timed out or dropped while communicating with the AI provider. Please verify your provider API key, token balance, and server connectivity.";
        }
        bubbleContent.innerHTML = '<p style="color: var(--color-danger, #ef4444); margin: 0; line-height: 1.5;"><strong>Error:</strong> ' + errorDisplay + "</p>";
        if (self.statusEl) self.statusEl.textContent = "Error";
      });
    },

    // Public alias used by ALLOW_SECRETS button handler
    sendMessage: function (msg) { this.send(msg); },

    _updateActivityPanel: function (panelEl, items, activity) {
      var key = activity.tool;
      var TOOL_SVGS = {
        "globe": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>',
        "route": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="19" r="3"></circle><path d="M9 19h8.5a4.5 4.5 0 0 0 0-9H10a4.5 4.5 0 0 1 0-9H18"></path></svg>',
        "list": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>',
        "box": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path></svg>',
        "file-text": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>',
        "database": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>',
        "folder": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>',
        "file": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>',
        "cpu": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"></rect><rect x="9" y="9" width="6" height="6"></rect><line x1="9" y1="1" x2="9" y2="4"></line><line x1="15" y1="1" x2="15" y2="4"></line><line x1="9" y1="20" x2="9" y2="23"></line><line x1="15" y1="20" x2="15" y2="23"></line><line x1="20" y1="9" x2="23" y2="9"></line><line x1="20" y1="14" x2="23" y2="14"></line><line x1="1" y1="9" x2="4" y2="9"></line><line x1="1" y1="14" x2="4" y2="14"></line></svg>',
        "layers": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>',
        "search": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>',
        "book-open": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>',
        "activity": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>',
      };
      var iconSvg = TOOL_SVGS[activity.icon] || '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>';

      if (activity.status === "start") {
        var item = document.createElement("div");
        item.className = "ai-activity-item ai-activity-item--loading";
        item.innerHTML = (
          '<span class="ai-activity-spinner"></span>' +
          '<span class="ai-activity-icon">' + iconSvg + '</span>' +
          '<span class="ai-activity-label">' + activity.label +
          (activity.detail ? ' <span class="ai-activity-detail">' + activity.detail + '</span>' : '') +
          '</span>' +
          '<span class="ai-activity-status">...</span>'
        );
        panelEl.appendChild(item);
        items[key] = item;
        panelEl.style.display = "flex";
      } else if (items[key]) {
        var el = items[key];
        el.classList.remove("ai-activity-item--loading");
        if (activity.status === "done") {
          el.classList.add("ai-activity-item--done");
          el.querySelector(".ai-activity-spinner").style.display = "none";
          el.querySelector(".ai-activity-status").innerHTML = '<span class="ai-sec-dot ai-sec-dot--ok"></span>';
        } else if (activity.status === "error") {
          el.classList.add("ai-activity-item--error");
          el.querySelector(".ai-activity-spinner").style.display = "none";
          el.querySelector(".ai-activity-status").innerHTML = '<span class="ai-sec-dot ai-sec-dot--critical"></span>';
        }
      }
      this._scrollToBottom();
    },

    _finishStreaming: function (bubble, activityPanel, text, start, activityItems) {
      this.isStreaming = false;
      this.sendBtnEl.style.display = "flex";
      this.stopBtnEl.style.display = "none";
      bubble.innerHTML = this.renderMarkdown(text);
      if (this.statusEl) this.statusEl.textContent = (Date.now() - start) + "ms";
      if (window.AiHelperDecisionBar) {
        window.AiHelperDecisionBar.extractAndShow(text);
      }
      if (text.trim() && window.AiHelperCache) {
        window.AiHelperCache.appendMessage(this.sessionId, { role: "assistant", content: text, created_at: new Date().toISOString() }, { title: this.sessionTitle, taskType: this.activeTaskType, context: this.activeContext });
      }
      if (window.AiHelperActions) window.AiHelperActions.checkLongMessages(this.messagesEl);
      // Collapse activity panel into summary toggle
      if (activityPanel && activityPanel.children.length > 0) {
        var items = activityPanel.querySelectorAll(".ai-activity-item");
        var count = items.length || activityPanel.children.length;
        activityPanel.classList.add("ai-activity-panel--done");
        var summary = document.createElement("button");
        summary.type = "button";
        summary.className = "ai-activity-summary-toggle";
        var SEARCH_SVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px;"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>';

        // Extract sample names of tools/files/URLs for summary label
        var usedDetails = [];
        for (var j = 0; j < items.length; j++) {
          var detailEl = items[j].querySelector(".ai-activity-detail");
          var labelEl = items[j].querySelector(".ai-activity-label");
          var txt = detailEl ? detailEl.textContent.trim() : (labelEl ? labelEl.textContent.trim() : "");
          if (txt && usedDetails.indexOf(txt) === -1 && usedDetails.length < 3) {
            usedDetails.push(txt);
          }
        }
        var detailSummary = usedDetails.length > 0 ? " (" + usedDetails.join(", ") + ")" : "";
        var labelText = "Sources & Tools Consulted (" + count + ")" + detailSummary;

        summary.innerHTML = SEARCH_SVG + " " + labelText + ' <span class="ai-activity-chevron" style="margin-left:auto;">▾</span>';
        summary.addEventListener("click", function () {
          var expanded = activityPanel.getAttribute("data-expanded") === "true";
          activityPanel.setAttribute("data-expanded", expanded ? "false" : "true");
          var chevron = summary.querySelector(".ai-activity-chevron");
          if (chevron) chevron.textContent = expanded ? "▾" : "▴";
        });
        activityPanel.setAttribute("data-expanded", "false");
        activityPanel.insertBefore(summary, activityPanel.firstChild);
      }
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
          self.open({
            context: p.getAttribute("data-ai-context"),
            initialPrompt: p.getAttribute("data-ai-prompt"),
            taskType: p.getAttribute("data-ai-task") || "general",
            fresh: p.getAttribute("data-ai-fresh") === "true",
          });
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

      var isCreatePage = window.location.pathname.indexOf("/apps/create") !== -1 || window.location.pathname.indexOf("/railpack-apps/create") !== -1 || window.location.pathname.indexOf("/apps/new") !== -1;
      if (opts.fresh || isCreatePage) {
        var cachedMsgs = window.AiHelperCache ? window.AiHelperCache.getCachedMessages(this.sessionId) : [];
        if (cachedMsgs && cachedMsgs.length > 0) {
          this.startNewChat({ taskType: opts.taskType || (isCreatePage ? "app_deploy" : this.activeTaskType), context: opts.context || (isCreatePage ? "new_app" : this.activeContext), initialPrompt: opts.initialPrompt });
          return;
        }
      }

      if (opts.split) {
        this.drawerEl.classList.add("ai-helper-drawer--split");
        this.backdropEl.classList.remove("active");
        this.backdropEl.classList.add("ai-helper-backdrop--split");
        document.body.classList.add("apps-engine-ai-active", "ai-helper-split-active");
        document.querySelectorAll(".apps-engine-optic").forEach(function (el) {
          el.classList.add("is-ai-mode");
        });
        window.dispatchEvent(new CustomEvent("ai-helper:mode-change", { detail: { split: true, active: true } }));
      } else {
        this.drawerEl.classList.remove("ai-helper-drawer--split");
        this.backdropEl.classList.remove("ai-helper-backdrop--split");
        this.backdropEl.classList.add("active");
        document.body.classList.remove("apps-engine-ai-active", "ai-helper-split-active");
        document.querySelectorAll(".apps-engine-optic").forEach(function (el) {
          el.classList.remove("is-ai-mode");
        });
        window.dispatchEvent(new CustomEvent("ai-helper:mode-change", { detail: { split: false, active: true } }));
      }

      this.drawerEl.classList.add("open");
      this.closeHistoryView();
      this.inputEl.focus();
      if (opts.initialPrompt) {
        var msgs = window.AiHelperCache ? window.AiHelperCache.getCachedMessages(this.sessionId) : [];
        if (msgs && msgs.length > 0) this.startNewChat({ taskType: opts.taskType || this.activeTaskType, context: opts.context || this.activeContext, initialPrompt: opts.initialPrompt });
        else this.send(opts.initialPrompt);
      }
    },

    close: function () {
      if (this.drawerEl) {
        this.drawerEl.classList.remove("open");
        this.drawerEl.classList.remove("ai-helper-drawer--split");
      }
      if (this.backdropEl) {
        this.backdropEl.classList.remove("active");
        this.backdropEl.classList.remove("ai-helper-backdrop--split");
      }
      document.body.classList.remove("apps-engine-ai-active", "ai-helper-split-active");
      document.querySelectorAll(".apps-engine-optic").forEach(function (el) {
        el.classList.remove("is-ai-mode");
      });
      window.dispatchEvent(new CustomEvent("ai-helper:mode-change", { detail: { split: false, active: false } }));
      this.closeHistoryView();
    },


    toggle: function () {
      if (this.drawerEl && this.drawerEl.classList.contains("open")) this.close();
      else this.open();
    },

    explainError: function (errText, opts) {
      opts = opts || {};
      this.open({ taskType: "error_diag", context: opts.context || "Error Diagnostic", initialPrompt: "Here is an error log from my server/application. Please explain what caused it and give me the exact step-by-step fix:\n\n```\n" + (errText || "").trim().slice(-16000) + "\n```" });
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
