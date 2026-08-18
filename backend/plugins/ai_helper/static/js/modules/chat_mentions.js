/**
 * chat_mentions.js — Autocomplete Mentions (@) and Slash Commands (/) for AI Assistant.
 * Provides instant record discovery (domains, apps, databases, files) and command shortcuts.
 */
(function () {
  "use strict";

  var SLASH_COMMANDS = [
    {
      cmd: "/explain",
      label: "Explain Error / Logs",
      desc: "Paste error logs to get root cause & fix",
      template: "Please explain what caused this error and give me step-by-step fix:\n\n```\n<paste error here>\n```",
      task: "error_diag",
    },
    {
      cmd: "/docker",
      label: "Docker / Dockerfile Setup",
      desc: "Generate Dockerfile or Compose configuration",
      template: "How do I configure Dockerfile & docker-compose for this service?",
      task: "app",
    },
    {
      cmd: "/nginx",
      label: "Nginx Reverse Proxy",
      desc: "Configure reverse proxy, headers, and SSL",
      template: "How do I configure Nginx reverse proxy routing for this domain?",
      task: "domain",
    },
    {
      cmd: "/db",
      label: "Database Diagnostics & SQL",
      desc: "Troubleshoot DB connection or schema",
      template: "Help me troubleshoot the database connection and configuration.",
      task: "database",
    },
    {
      cmd: "/ssl",
      label: "SSL & HTTPS Certificate",
      desc: "Troubleshoot Let's Encrypt / SSL certificates",
      template: "How do I fix SSL / HTTPS certificate issues for this domain?",
      task: "domain",
    },
    {
      cmd: "/system",
      label: "VPS & System Health",
      desc: "Analyze CPU, memory, disk, and processes",
      template: "How do I check and optimize VPS memory and disk performance?",
      task: "system",
    },
    {
      cmd: "/clear",
      label: "Clear Current Chat",
      desc: "Reset conversation history",
      action: "clear",
    },
    {
      cmd: "/new",
      label: "Start New Chat",
      desc: "Open a fresh conversation tab",
      action: "new",
    },
    {
      cmd: "/help",
      label: "Shortcuts & Mention Tips",
      desc: "View how to use @ and / shortcuts",
      template: "What can you help me with? Show me what tasks and panel tools you can inspect.",
      task: "general",
    },
  ];

  var AiHelperMentions = {
    inputEl: null,
    popupEl: null,
    resources: null,
    items: [],
    activeIndex: 0,
    isOpen: false,
    triggerType: null, // '@' or '/'
    triggerIndex: -1,
    query: "",

    init: function (inputEl, containerEl) {
      this.inputEl = inputEl;
      if (!this.inputEl) return;

      var self = this;
      this._injectPopup(containerEl || inputEl.parentElement);
      this._loadResources();

      this.inputEl.addEventListener("input", function () {
        self._handleInput();
      });

      this.inputEl.addEventListener("keydown", function (e) {
        if (!self.isOpen) return;

        if (e.key === "ArrowDown") {
          e.preventDefault();
          self.activeIndex = (self.activeIndex + 1) % self.items.length;
          self._updateActiveItem();
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          self.activeIndex = (self.activeIndex - 1 + self.items.length) % self.items.length;
          self._updateActiveItem();
        } else if (e.key === "Enter" || e.key === "Tab") {
          if (self.items.length > 0) {
            e.preventDefault();
            e.stopPropagation();
            self._selectItem(self.items[self.activeIndex]);
          }
        } else if (e.key === "Escape") {
          e.preventDefault();
          self.close();
        }
      });

      document.addEventListener("click", function (e) {
        if (self.isOpen && !self.popupEl.contains(e.target) && e.target !== self.inputEl) {
          self.close();
        }
      });
    },

    _injectPopup: function (parent) {
      if (document.getElementById("ai-mention-popup")) {
        this.popupEl = document.getElementById("ai-mention-popup");
        return;
      }

      var popup = document.createElement("div");
      popup.id = "ai-mention-popup";
      popup.className = "ai-mention-popup";
      popup.style.display = "none";
      parent.appendChild(popup);
      this.popupEl = popup;
    },

    _loadResources: function () {
      var self = this;
      fetch("/plugins/ai_helper/api/resources")
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (data) {
          if (data && data.status === "ok" && data.resources) {
            self.resources = data.resources;
          }
        })
        .catch(function () {});
    },

    _handleInput: function () {
      var val = this.inputEl.value;
      var selStart = this.inputEl.selectionStart || val.length;
      var textBeforeCaret = val.substring(0, selStart);

      // Check for @ or / at the last word
      var lastAt = textBeforeCaret.lastIndexOf("@");
      var lastSlash = textBeforeCaret.lastIndexOf("/");

      var trigger = null;
      var triggerIdx = -1;

      if (lastAt >= 0 && (lastAt === 0 || /\s/.test(textBeforeCaret[lastAt - 1]))) {
        trigger = "@";
        triggerIdx = lastAt;
      }

      if (lastSlash >= 0 && (lastSlash === 0 || /\s/.test(textBeforeCaret[lastSlash - 1]))) {
        if (lastSlash > triggerIdx) {
          trigger = "/";
          triggerIdx = lastSlash;
        }
      }

      if (!trigger || triggerIdx < 0) {
        this.close();
        return;
      }

      var query = textBeforeCaret.substring(triggerIdx + 1);
      // If query contains spaces or newline, close popup
      if (/[\s\n]/.test(query)) {
        this.close();
        return;
      }

      this.triggerType = trigger;
      this.triggerIndex = triggerIdx;
      this.query = query.toLowerCase();

      this._renderOptions();
    },

    _renderOptions: function () {
      var self = this;
      var filtered = [];

      if (this.triggerType === "@") {
        var r = this.resources || { domains: [], apps: [], databases: [], file_targets: [] };

        // Domains
        (r.domains || []).forEach(function (d) {
          if (!self.query || d.name.toLowerCase().indexOf(self.query) !== -1) {
            filtered.push({
              type: "domain",
              icon: "🌐",
              category: "Domain",
              key: "@domain:" + d.name,
              label: d.name,
              badge: d.project_type || "proxy",
              context: "Domain " + d.name,
              task: "domain",
            });
          }
        });

        // Apps
        (r.apps || []).forEach(function (a) {
          if (!self.query || a.name.toLowerCase().indexOf(self.query) !== -1 || a.type.toLowerCase().indexOf(self.query) !== -1) {
            filtered.push({
              type: "app",
              icon: "📦",
              category: "App",
              key: "@app:" + a.name,
              label: a.name,
              badge: a.type + (a.status ? " · " + a.status : ""),
              context: "App " + a.name + " (" + a.type + ")",
              task: "app",
            });
          }
        });

        // Databases
        (r.databases || []).forEach(function (db) {
          if (!self.query || db.name.toLowerCase().indexOf(self.query) !== -1) {
            filtered.push({
              type: "database",
              icon: "🗄️",
              category: "Database",
              key: "@db:" + db.name,
              label: db.name,
              badge: db.engine || "SQL",
              context: "Database " + db.name,
              task: "database",
            });
          }
        });

        // File Targets
        (r.file_targets || []).forEach(function (f) {
          var name = f.domain || f.preset || f.id;
          if (!self.query || name.toLowerCase().indexOf(self.query) !== -1) {
            filtered.push({
              type: "file",
              icon: "📁",
              category: "File Target",
              key: "@file:" + (f.id || name),
              label: name,
              badge: f.type || "files",
              context: "File Target " + name,
              task: "file_manager",
            });
          }
        });
      } else if (this.triggerType === "/") {
        SLASH_COMMANDS.forEach(function (cmd) {
          if (!self.query || cmd.cmd.toLowerCase().indexOf(self.query) !== -1 || cmd.label.toLowerCase().indexOf(self.query) !== -1) {
            filtered.push({
              type: "command",
              icon: "⚡",
              category: "Command",
              key: cmd.cmd,
              label: cmd.cmd,
              badge: cmd.label,
              desc: cmd.desc,
              template: cmd.template,
              action: cmd.action,
              task: cmd.task,
            });
          }
        });
      }

      this.items = filtered.slice(0, 10);
      if (this.items.length === 0) {
        this.close();
        return;
      }

      this.activeIndex = 0;
      this._buildDOM();
      this.popupEl.style.display = "flex";
      this.isOpen = true;
    },

    _buildDOM: function () {
      var self = this;
      this.popupEl.innerHTML = "";

      var titleRow = document.createElement("div");
      titleRow.className = "ai-mention-header";
      titleRow.textContent = this.triggerType === "@" ? "Mention System Record (@)" : "Quick Slash Commands (/)";
      this.popupEl.appendChild(titleRow);

      var listEl = document.createElement("div");
      listEl.className = "ai-mention-list";

      this.items.forEach(function (item, idx) {
        var row = document.createElement("div");
        row.className = "ai-mention-item" + (idx === self.activeIndex ? " ai-mention-item--active" : "");
        row.innerHTML = [
          '  <span class="ai-mention-icon">' + item.icon + "</span>",
          '  <div class="ai-mention-info">',
          '    <div class="ai-mention-name-row">',
          '      <span class="ai-mention-name">' + self._escape(item.label) + "</span>",
          item.badge ? '      <span class="ai-mention-badge">' + self._escape(item.badge) + "</span>" : "",
          "    </div>",
          item.desc ? '    <span class="ai-mention-desc">' + self._escape(item.desc) + "</span>" : "",
          "  </div>",
        ].join("\n");

        row.addEventListener("mouseenter", function () {
          self.activeIndex = idx;
          self._updateActiveItem();
        });

        row.addEventListener("mousedown", function (e) {
          e.preventDefault();
          self._selectItem(item);
        });

        listEl.appendChild(row);
      });

      this.popupEl.appendChild(listEl);
    },

    _updateActiveItem: function () {
      var all = this.popupEl.querySelectorAll(".ai-mention-item");
      for (var i = 0; i < all.length; i++) {
        if (i === this.activeIndex) all[i].classList.add("ai-mention-item--active");
        else all[i].classList.remove("ai-mention-item--active");
      }
    },

    _selectItem: function (item) {
      if (!item) return;

      var val = this.inputEl.value;
      var before = val.substring(0, this.triggerIndex);
      var after = val.substring(this.triggerIndex + 1 + this.query.length);

      if (item.type === "command") {
        if (item.action === "clear") {
          if (window.AiHelper) window.AiHelper.startNewChat();
          this.inputEl.value = "";
          this.close();
          return;
        }
        if (item.action === "new") {
          if (window.AiHelper) window.AiHelper.startNewChat({ taskType: "general" });
          this.inputEl.value = "";
          this.close();
          return;
        }

        this.inputEl.value = item.template || (item.key + " ");
        if (item.task && window.AiHelper) {
          window.AiHelper.setTaskType(item.task);
        }
      } else {
        // Mention (@)
        var insertText = item.key + " ";
        this.inputEl.value = before + insertText + after;
        var newPos = before.length + insertText.length;
        this.inputEl.setSelectionRange(newPos, newPos);

        if (item.context && window.AiHelper) {
          window.AiHelper.setContext(item.context);
        }
        if (item.task && window.AiHelper) {
          window.AiHelper.setTaskType(item.task);
        }
      }

      this.inputEl.dispatchEvent(new Event("input", { bubbles: true }));
      this.inputEl.focus();
      this.close();
    },

    close: function () {
      if (this.popupEl) this.popupEl.style.display = "none";
      this.isOpen = false;
      this.items = [];
      this.triggerType = null;
      this.triggerIndex = -1;
      this.query = "";
    },

    _escape: function (str) {
      if (!str) return "";
      return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    },
  };

  window.AiHelperMentions = AiHelperMentions;
})();
