/**
 * chat_models.js — Model Switcher Modal & Provider Selector for AI Assistant.
 */
(function () {
  "use strict";

  var AiHelperModels = {
    modalEl: null,
    listEl: null,
    viewportEl: null,
    triggerBtnEl: null,
    triggerTextEl: null,
    arrowUpEl: null,
    arrowDownEl: null,
    providers: [],
    availableTargets: [],
    selectedProviderId: null,
    selectedModelName: null,
    onSelectModel: null,

    init: function (triggerBtnId, modalId, onSelectModel) {
      this.triggerBtnEl = document.getElementById(triggerBtnId);
      this.triggerTextEl = document.getElementById(triggerBtnId + "-text");
      this.modalEl = document.getElementById(modalId);
      this.onSelectModel = onSelectModel;

      if (!this.modalEl) return;

      this.listEl = document.getElementById("ai-helper-model-list");
      this.viewportEl = document.getElementById("ai-helper-model-viewport");
      this.arrowUpEl = document.getElementById("ai-helper-model-arrow-up");
      this.arrowDownEl = document.getElementById("ai-helper-model-arrow-down");

      var self = this;

      if (this.triggerBtnEl) {
        this.triggerBtnEl.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          self.toggle();
        });
      }

      var backdrop = document.getElementById("ai-helper-model-modal-backdrop");
      if (backdrop) {
        backdrop.addEventListener("click", function () {
          self.close();
        });
      }

      if (this.arrowUpEl && this.viewportEl) {
        this.arrowUpEl.addEventListener("click", function (e) {
          e.stopPropagation();
          self.viewportEl.scrollBy({ top: -38, behavior: "smooth" });
        });
      }

      if (this.arrowDownEl && this.viewportEl) {
        this.arrowDownEl.addEventListener("click", function (e) {
          e.stopPropagation();
          self.viewportEl.scrollBy({ top: 38, behavior: "smooth" });
        });
      }

      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && self.isOpen()) {
          self.close();
        }
      });

      this.loadProviders();
    },

    loadProviders: function () {
      var self = this;
      fetch("/plugins/ai_helper/api/providers")
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data && data.status === "ok" && Array.isArray(data.providers)) {
            self.providers = data.providers;
            self._buildAvailableTargets();
            self._restoreOrSetDefault();
            self.render();
          }
        })
        .catch(function () {});
    },

    _buildAvailableTargets: function () {
      var targets = [];
      this.providers.forEach(function (p) {
        if (p.is_enabled === false) return;
        var models = [];
        if (Array.isArray(p.models) && p.models.length > 0) {
          models = p.models.map(function (m) { return String(m).trim(); }).filter(Boolean);
        } else if (p.models_list && typeof p.models_list === "string") {
          models = p.models_list.split(",").map(function (m) { return m.trim(); }).filter(Boolean);
        }
        if (p.model_name && models.indexOf(p.model_name) === -1) {
          models.unshift(p.model_name);
        }
        if (models.length === 0) {
          models = [p.model_name || "gpt-4o-mini"];
        }

        models.forEach(function (model) {
          targets.push({
            provider_id: p.id,
            provider_name: p.name,
            provider_type: p.provider_type,
            model_name: model,
            is_default: p.is_default,
          });
        });
      });
      this.availableTargets = targets;
    },

    _restoreOrSetDefault: function () {
      var cache = window.AiHelperCache;
      var saved = cache ? cache.getSelectedTarget() : null;

      if (saved && saved.indexOf(":") !== -1) {
        var parts = saved.split(":");
        var pId = parseInt(parts[0], 10);
        var mName = parts.slice(1).join(":");
        var exists = this.availableTargets.some(function (t) {
          return t.provider_id === pId && t.model_name === mName;
        });
        if (exists) {
          this.selectedProviderId = pId;
          this.selectedModelName = mName;
          this._updateTriggerDisplay();
          return;
        }
      }

      if (this.availableTargets.length > 0) {
        var defaultTarget = this.availableTargets.find(function (t) { return t.is_default; }) || this.availableTargets[0];
        this.selectedProviderId = defaultTarget.provider_id;
        this.selectedModelName = defaultTarget.model_name;
        this._updateTriggerDisplay();
      }
    },

    _updateTriggerDisplay: function () {
      if (this.triggerTextEl) {
        this.triggerTextEl.textContent = this.selectedModelName || "Select Model";
      }
      if (this.onSelectModel) {
        this.onSelectModel(this.selectedProviderId, this.selectedModelName);
      }
    },

    render: function () {
      var self = this;
      if (!this.listEl) return;

      if (this.availableTargets.length === 0) {
        this.listEl.innerHTML = '<div class="ai-helper-model-text-item" style="color:var(--color-muted);">No enabled models</div>';
        return;
      }

      this.listEl.innerHTML = "";
      this.availableTargets.forEach(function (t) {
        var isActive = t.provider_id === self.selectedProviderId && t.model_name === self.selectedModelName;
        var item = document.createElement("button");
        item.type = "button";
        item.className = "ai-helper-model-text-item" + (isActive ? " ai-helper-model-text-item--active" : "");
        item.title = t.provider_name + " (" + t.model_name + ")";

        item.innerHTML = '<span class="ai-helper-model-title">' + self._escapeHtml(t.model_name) + "</span>";

        item.addEventListener("click", function (e) {
          e.stopPropagation();
          self.selectTarget(t.provider_id, t.model_name);
        });

        self.listEl.appendChild(item);
      });
    },

    selectTarget: function (providerId, modelName) {
      this.selectedProviderId = providerId;
      this.selectedModelName = modelName;

      var cache = window.AiHelperCache;
      if (cache) {
        cache.setSelectedTarget(providerId + ":" + modelName);
      }

      this._updateTriggerDisplay();
      this.render();
      this.close();
    },

    open: function () {
      if (this.modalEl) {
        this.modalEl.classList.add("open");
        this.render();
        this._scrollToActive();
      }
    },

    close: function () {
      if (this.modalEl) {
        this.modalEl.classList.remove("open");
      }
    },

    toggle: function () {
      if (this.isOpen()) this.close();
      else this.open();
    },

    isOpen: function () {
      return this.modalEl && this.modalEl.classList.contains("open");
    },

    _scrollToActive: function () {
      if (!this.listEl || !this.viewportEl) return;
      var activeEl = this.listEl.querySelector(".ai-helper-model-text-item--active");
      if (activeEl) {
        var topPos = activeEl.offsetTop - (this.viewportEl.clientHeight / 2) + (activeEl.clientHeight / 2);
        this.viewportEl.scrollTop = Math.max(0, topPos);
      }
    },

    _escapeHtml: function (text) {
      if (!text) return "";
      return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    },
  };

  window.AiHelperModels = AiHelperModels;
})();
