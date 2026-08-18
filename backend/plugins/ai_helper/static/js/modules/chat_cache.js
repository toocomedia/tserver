/**
 * chat_cache.js — LocalStorage Cache for AI Helper conversation history & sessions.
 */
(function () {
  "use strict";

  var CACHE_KEY = "ai_helper_cache_v2";
  var SESSION_KEY = "ai_helper_session_id";
  var TARGET_KEY = "ai_helper_selected_target";

  var AiHelperCache = {
    getCache: function () {
      try {
        var raw = localStorage.getItem(CACHE_KEY);
        return raw ? JSON.parse(raw) : { activeSessionId: null, sessions: {} };
      } catch (e) {
        return { activeSessionId: null, sessions: {} };
      }
    },

    saveCache: function (cache) {
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
      } catch (e) {}
    },

    getActiveSessionId: function () {
      var cache = this.getCache();
      return localStorage.getItem(SESSION_KEY) || cache.activeSessionId;
    },

    setActiveSessionId: function (sessionId) {
      localStorage.setItem(SESSION_KEY, sessionId);
      var cache = this.getCache();
      cache.activeSessionId = sessionId;
      this.saveCache(cache);
    },

    getCachedMessages: function (sessionId) {
      var cache = this.getCache();
      if (cache.sessions && cache.sessions[sessionId] && Array.isArray(cache.sessions[sessionId].messages)) {
        return cache.sessions[sessionId].messages;
      }
      return [];
    },

    appendMessage: function (sessionId, msgObj, metadata) {
      var cache = this.getCache();
      if (!cache.sessions) cache.sessions = {};
      if (!cache.sessions[sessionId]) {
        cache.sessions[sessionId] = {
          session_id: sessionId,
          title: metadata.title || "New Chat",
          task_type: metadata.taskType || "general",
          context_key: metadata.context || null,
          messages: [],
          updated_at: new Date().toISOString(),
        };
      }

      cache.sessions[sessionId].messages.push(msgObj);
      cache.sessions[sessionId].updated_at = new Date().toISOString();
      if (metadata.title) cache.sessions[sessionId].title = metadata.title;
      if (metadata.taskType) cache.sessions[sessionId].task_type = metadata.taskType;
      cache.activeSessionId = sessionId;

      this.saveCache(cache);
    },

    setSession: function (sessionId, sessionData, messages) {
      var cache = this.getCache();
      if (!cache.sessions) cache.sessions = {};
      cache.sessions[sessionId] = {
        session_id: sessionId,
        title: sessionData.title || "New Chat",
        task_type: sessionData.task_type || "general",
        context_key: sessionData.context_key || null,
        messages: messages || (cache.sessions[sessionId] ? cache.sessions[sessionId].messages : []),
        updated_at: sessionData.updated_at || new Date().toISOString(),
      };
      this.saveCache(cache);
    },

    removeSession: function (sessionId) {
      var cache = this.getCache();
      if (cache.sessions && cache.sessions[sessionId]) {
        delete cache.sessions[sessionId];
      }
      this.saveCache(cache);
    },

    clearAll: function () {
      localStorage.removeItem(SESSION_KEY);
      this.saveCache({ activeSessionId: null, sessions: {} });
    },

    getSelectedTarget: function () {
      return localStorage.getItem(TARGET_KEY) || null;
    },

    setSelectedTarget: function (targetVal) {
      localStorage.setItem(TARGET_KEY, targetVal);
    },
  };

  window.AiHelperCache = AiHelperCache;
})();
