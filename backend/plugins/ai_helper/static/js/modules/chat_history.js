/**
 * chat_history.js — History panel, task category filter, search, and session card manager.
 */
(function () {
  "use strict";

  var AiHelperHistory = {
    cachedServerSessions: null,
    activeTaskFilter: "all",
    searchQuery: "",
    panelEl: null,
    listEl: null,
    searchEl: null,

    init: function (panelEl, listEl, searchEl, onSelectSession, onDeleteSession, onClearAll) {
      this.panelEl = panelEl;
      this.listEl = listEl;
      this.searchEl = searchEl;
      this.onSelectSession = onSelectSession;
      this.onDeleteSession = onDeleteSession;
      this.onClearAll = onClearAll;

      var self = this;

      if (this.searchEl) {
        this.searchEl.addEventListener("input", function (e) {
          self.searchQuery = e.target.value.toLowerCase().trim();
          self.render();
        });
      }

      var chipContainer = document.getElementById("ai-helper-task-chips");
      if (chipContainer) {
        chipContainer.addEventListener("click", function (e) {
          var chip = e.target.closest(".ai-task-chip");
          if (!chip) return;
          chipContainer.querySelectorAll(".ai-task-chip").forEach(function (c) {
            c.classList.remove("active");
          });
          chip.classList.add("active");
          self.activeTaskFilter = chip.getAttribute("data-task-filter") || "all";
          self.render();
        });
      }
    },

    open: function () {
      if (this.panelEl) this.panelEl.classList.add("open");
      this.loadAndRender();
    },

    close: function () {
      if (this.panelEl) this.panelEl.classList.remove("open");
    },

    isOpen: function () {
      return this.panelEl && this.panelEl.classList.contains("open");
    },

    loadAndRender: function () {
      var self = this;
      this.render();

      fetch("/plugins/ai_helper/api/sessions")
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data && data.status === "ok" && Array.isArray(data.sessions)) {
            self.cachedServerSessions = data.sessions;
            self.render();
          }
        })
        .catch(function () {});
    },

    render: function () {
      var self = this;
      if (!this.listEl) return;

      var cache = window.AiHelperCache ? window.AiHelperCache.getCache() : { sessions: {} };
      var sessionMap = {};

      if (cache.sessions) {
        Object.keys(cache.sessions).forEach(function (id) {
          var s = cache.sessions[id];
          sessionMap[id] = {
            session_id: id,
            title: s.title || "New Chat",
            task_type: s.task_type || "general",
            updated_at: s.updated_at || new Date().toISOString(),
            message_count: s.messages ? s.messages.length : 0,
            last_message: s.messages && s.messages.length > 0 ? s.messages[s.messages.length - 1].content.slice(0, 80) : "",
          };
        });
      }

      if (this.cachedServerSessions) {
        this.cachedServerSessions.forEach(function (s) {
          sessionMap[s.session_id] = Object.assign({}, sessionMap[s.session_id] || {}, s);
        });
      }

      var list = Object.values(sessionMap);
      list.sort(function (a, b) {
        return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
      });

      if (this.activeTaskFilter && this.activeTaskFilter !== "all") {
        list = list.filter(function (s) {
          return (s.task_type || "general").toLowerCase() === self.activeTaskFilter.toLowerCase();
        });
      }

      if (this.searchQuery) {
        list = list.filter(function (s) {
          var titleMatch = (s.title || "").toLowerCase().indexOf(self.searchQuery) !== -1;
          var msgMatch = (s.last_message || "").toLowerCase().indexOf(self.searchQuery) !== -1;
          return titleMatch || msgMatch;
        });
      }

      if (list.length === 0) {
        this.listEl.innerHTML = '<div class="ai-history-empty"><p>No conversations found.</p></div>';
        return;
      }

      this.listEl.innerHTML = "";
      var activeSessionId = window.AiHelper ? window.AiHelper.sessionId : null;
      var taskMeta = window.AiHelper ? window.AiHelper.TASK_META : {};

      list.forEach(function (s) {
        var meta = taskMeta[s.task_type] || { label: "General" };
        var isActive = s.session_id === activeSessionId;
        var timeStr = self._formatRelativeTime(s.updated_at);

        var card = document.createElement("div");
        card.className = "ai-history-card" + (isActive ? " active" : "");
        card.setAttribute("data-session-id", s.session_id);

        card.innerHTML = [
          '<div class="ai-history-card-header">',
          '  <span class="ai-history-card-title">' + self._escapeHtml(s.title || "New Chat") + "</span>",
          '  <button type="button" class="ai-history-card-del" title="Delete conversation">✕</button>',
          "</div>",
          '<div class="ai-history-card-footer">',
          '  <span class="ai-history-task-tag">' + self._escapeHtml(meta.label || "General") + "</span>",
          '  <span class="ai-history-card-meta">' + (s.message_count || 0) + " msgs · " + timeStr + "</span>",
          "</div>",
        ].join("\n");

        card.addEventListener("click", function () {
          if (self.onSelectSession) self.onSelectSession(s.session_id);
        });

        var delBtn = card.querySelector(".ai-history-card-del");
        delBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          if (self.onDeleteSession) self.onDeleteSession(s.session_id);
        });

        self.listEl.appendChild(card);
      });
    },

    _formatRelativeTime: function (isoString) {
      if (!isoString) return "Recently";
      var d = new Date(isoString);
      var now = new Date();
      var diffSec = Math.floor((now - d) / 1000);
      var diffMin = Math.floor(diffSec / 60);
      var diffHr = Math.floor(diffMin / 60);
      var diffDay = Math.floor(diffHr / 24);

      if (diffSec < 45) return "Just now";
      if (diffMin < 60) return diffMin + "m ago";
      if (diffHr < 24) return diffHr + "h ago";
      if (diffDay === 1) return "Yesterday";
      if (diffDay < 7) return diffDay + "d ago";
      return (d.getMonth() + 1) + "/" + d.getDate();
    },

    _escapeHtml: function (text) {
      if (!text) return "";
      return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    },
  };

  window.AiHelperHistory = AiHelperHistory;
})();
