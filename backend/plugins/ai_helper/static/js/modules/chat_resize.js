/**
 * chat_resize.js — Drag-to-resize controller for AI Assistant Drawer.
 * Supports smooth mouse/touch dragging, min/max bounds, RTL support, and localStorage persistence.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "ai_helper_drawer_width";
  var MIN_WIDTH = 340;
  var DEFAULT_WIDTH = 440;

  var AiHelperResize = {
    drawerEl: null,
    handleEl: null,
    isDragging: false,
    startX: 0,
    startWidth: 0,

    init: function (drawerEl) {
      if (!drawerEl) return;
      this.drawerEl = drawerEl;

      // Create and append resize handle on drawer edge
      var handle = document.createElement("div");
      handle.className = "ai-helper-resize-handle";
      handle.setAttribute("title", "Drag to resize AI Assistant");
      drawerEl.appendChild(handle);
      this.handleEl = handle;

      // Restore saved width from localStorage
      this.restoreWidth();

      var self = this;

      // Mouse drag events
      handle.addEventListener("mousedown", function (e) {
        self.startDrag(e.clientX);
        e.preventDefault();
      });

      // Touch drag events
      handle.addEventListener("touchstart", function (e) {
        if (e.touches && e.touches.length > 0) {
          self.startDrag(e.touches[0].clientX);
        }
      }, { passive: true });

      window.addEventListener("mousemove", function (e) {
        if (!self.isDragging) return;
        self.onDrag(e.clientX);
      });

      window.addEventListener("touchmove", function (e) {
        if (!self.isDragging || !e.touches || e.touches.length === 0) return;
        self.onDrag(e.touches[0].clientX);
      }, { passive: true });

      window.addEventListener("mouseup", function () {
        if (self.isDragging) self.stopDrag();
      });

      window.addEventListener("touchend", function () {
        if (self.isDragging) self.stopDrag();
      });
    },

    restoreWidth: function () {
      var saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        var width = parseInt(saved, 10);
        if (!isNaN(width) && width >= MIN_WIDTH) {
          var maxWidth = Math.min(window.innerWidth * 0.94, 1200);
          var clamped = Math.max(MIN_WIDTH, Math.min(width, maxWidth));
          this.drawerEl.style.width = clamped + "px";
        }
      }
    },

    startDrag: function (clientX) {
      this.isDragging = true;
      this.startX = clientX;
      this.startWidth = this.drawerEl.getBoundingClientRect().width;
      this.handleEl.classList.add("resizing");
      document.body.classList.add("ai-helper-resizing");
      this.drawerEl.style.transition = "none";
    },

    onDrag: function (clientX) {
      var isRtl = document.documentElement.getAttribute("dir") === "rtl";
      var delta = isRtl ? (clientX - this.startX) : (this.startX - clientX);
      var newWidth = this.startWidth + delta;

      var maxWidth = Math.min(window.innerWidth * 0.94, 1200);
      var clamped = Math.max(MIN_WIDTH, Math.min(newWidth, maxWidth));

      this.drawerEl.style.width = clamped + "px";
    },

    stopDrag: function () {
      if (!this.isDragging) return;
      this.isDragging = false;
      this.handleEl.classList.remove("resizing");
      document.body.classList.remove("ai-helper-resizing");
      this.drawerEl.style.transition = "";

      var finalWidth = Math.round(this.drawerEl.getBoundingClientRect().width);
      try {
        localStorage.setItem(STORAGE_KEY, finalWidth);
      } catch (e) {}
    },
  };

  window.AiHelperResize = AiHelperResize;
})();
