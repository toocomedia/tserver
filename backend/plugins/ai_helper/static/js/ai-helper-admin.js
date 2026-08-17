/**
 * ai-helper-admin.js — AI Helper Admin & Provider Management Controller
 */
(function (window, document) {
  "use strict";

  var PRESET_CONFIGS = {
    openai: {
      name: "OpenAI",
      provider_type: "openai_compatible",
      base_url: "https://api.openai.com/v1",
      models: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o3-mini", "o1-preview"],
      default_model: "gpt-4o-mini",
    },
    anthropic: {
      name: "Anthropic Claude",
      provider_type: "anthropic",
      base_url: "https://api.anthropic.com/v1",
      models: ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
      default_model: "claude-3-5-sonnet-20241022",
    },
    openrouter: {
      name: "OpenRouter",
      provider_type: "openai_compatible",
      base_url: "https://openrouter.ai/api/v1",
      models: ["deepseek/deepseek-chat", "anthropic/claude-3.5-sonnet", "openai/gpt-4o-mini", "meta-llama/llama-3.3-70b-instruct"],
      default_model: "deepseek/deepseek-chat",
    },
    deepseek: {
      name: "DeepSeek",
      provider_type: "openai_compatible",
      base_url: "https://api.deepseek.com/v1",
      models: ["deepseek-chat", "deepseek-reasoner"],
      default_model: "deepseek-chat",
    },
    groq: {
      name: "Groq Cloud",
      provider_type: "openai_compatible",
      base_url: "https://api.groq.com/openai/v1",
      models: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
      default_model: "llama-3.3-70b-versatile",
    },
    gemini: {
      name: "Google Gemini",
      provider_type: "openai_compatible",
      base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
      models: ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
      default_model: "gemini-2.0-flash",
    },
    ollama: {
      name: "Local Ollama",
      provider_type: "openai_compatible",
      base_url: "http://localhost:11434/v1",
      models: ["llama3.2", "qwen2.5-coder", "mistral", "deepseek-r1"],
      default_model: "llama3.2",
    },
    custom: {
      name: "Custom OpenAI Compatible",
      provider_type: "openai_compatible",
      base_url: "",
      models: [],
      default_model: "",
    },
  };

  var AiHelperAdmin = {
    currentEditId: null,
    allKnownModels: [],
    enabledModels: [],
    defaultModel: "",
    fetchTimeout: null,
    lastFetchedKey: "",

    init: function () {
      this.cacheElements();
      this.bindEvents();
      this.checkUrlParams();
    },

    cacheElements: function () {
      this.drawerModal = document.getElementById("provider-drawer-modal");
      this.drawerForm = document.getElementById("provider-drawer-form");
      this.drawerTitle = document.getElementById("drawer-title");
      this.drawerSaveBtn = document.getElementById("drawer-btn-save");

      this.apiKeyInput = document.getElementById("drawer_api_key") || document.getElementById("api_key");
      this.providerNameInput = document.getElementById("drawer_provider_name") || document.getElementById("provider_name");
      this.baseUrlInput = document.getElementById("drawer_base_url") || document.getElementById("base_url");
      this.providerTypeSelect = document.getElementById("drawer_provider_type") || document.getElementById("provider_type");
      this.tempInput = document.getElementById("drawer_temperature") || document.getElementById("temperature");
      this.tokensInput = document.getElementById("drawer_max_tokens") || document.getElementById("max_tokens");
      this.rulesInput = document.getElementById("drawer_custom_rules") || document.getElementById("custom_rules");
      this.isDefaultCheck = document.getElementById("drawer_is_default") || document.getElementById("is_default");

      this.modelDropdownWrap = document.getElementById("drawer-model-dropdown-wrap") || document.getElementById("model-dropdown-wrap");
      this.modelTrigger = document.getElementById("drawer-model-trigger") || document.getElementById("model-trigger");
      this.modelTriggerText = document.getElementById("drawer-model-trigger-text") || document.getElementById("model-trigger-text");
      this.modelMenu = document.getElementById("drawer-model-menu") || document.getElementById("model-menu");
      this.filterModelInput = document.getElementById("drawer-filter-model-input") || document.getElementById("filter-model-input");
      this.btnAddModel = document.getElementById("drawer-btn-add-model") || document.getElementById("btn-add-model");
      this.btnFetchModels = document.getElementById("drawer-btn-fetch-models") || document.getElementById("btn-fetch-models");
      this.modelsListItems = document.getElementById("drawer-models-list-items") || document.getElementById("models-list-items");

      this.modelNameHidden = document.getElementById("drawer_model_name") || document.getElementById("model_name");
      this.modelsListHidden = document.getElementById("drawer_models_list") || document.getElementById("models_list");
      this.modelStatus = document.getElementById("drawer-model-status") || document.getElementById("model-status");
      this.testIndicator = document.getElementById("drawer-test-indicator") || document.getElementById("test-indicator");
      this.drawerTestBtn = document.getElementById("drawer-btn-test") || document.getElementById("btn-test-connection");
    },

    bindEvents: function () {
      var self = this;

      // Preset card selection
      document.querySelectorAll(".settings-choice").forEach(function (card) {
        card.addEventListener("click", function () {
          var key = this.getAttribute("data-preset-key");
          if (key) self.selectPresetCard(key);
          if (self.apiKeyInput && self.apiKeyInput.value.trim().length >= 6) {
            self.autoFetchModels();
          }
        });
      });

      // Model Dropdown Trigger Toggle
      if (this.modelTrigger && this.modelMenu) {
        this.modelTrigger.addEventListener("click", function (e) {
          e.stopPropagation();
          var isOpen = self.modelMenu.style.display === "block";
          self.modelMenu.style.display = isOpen ? "none" : "block";
          if (!isOpen && self.filterModelInput) {
            setTimeout(function () { self.filterModelInput.focus(); }, 50);
          }
        });

        document.addEventListener("click", function (e) {
          if (self.modelDropdownWrap && !self.modelDropdownWrap.contains(e.target)) {
            self.modelMenu.style.display = "none";
          }
        });

        this.modelMenu.addEventListener("click", function (e) {
          e.stopPropagation();
        });
      }

      // Filter search
      if (this.filterModelInput) {
        this.filterModelInput.addEventListener("input", function () {
          self.updateModelDropdownUI();
        });

        this.filterModelInput.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            e.preventDefault();
            if (self.btnAddModel) self.btnAddModel.click();
          }
        });
      }

      // Add Custom Model Button
      if (this.btnAddModel && this.filterModelInput) {
        this.btnAddModel.addEventListener("click", function () {
          var val = self.filterModelInput.value.trim();
          if (!val) return;
          if (!self.allKnownModels.includes(val)) {
            self.allKnownModels.unshift(val);
          }
          if (!self.enabledModels.includes(val)) {
            self.enabledModels.push(val);
          }
          if (!self.defaultModel) {
            self.defaultModel = val;
          }
          self.filterModelInput.value = "";
          self.updateModelDropdownUI();
        });
      }

      // Recheck / Fetch Models Button
      if (this.btnFetchModels) {
        this.btnFetchModels.addEventListener("click", function () {
          self.autoFetchModels(true);
        });
      }

      // API Key typing listener
      if (this.apiKeyInput) {
        this.apiKeyInput.addEventListener("input", function () {
          clearTimeout(self.fetchTimeout);
          var val = this.value.trim();
          if (val.length >= 6) {
            self.fetchTimeout = setTimeout(function () { self.autoFetchModels(); }, 500);
          }
        });

        this.apiKeyInput.addEventListener("blur", function () {
          if (this.value.trim().length >= 6) {
            self.autoFetchModels();
          }
        });
      }

      // Show/Hide password toggle
      var toggleKeyBtn = document.getElementById("drawer-btn-toggle-key") || document.getElementById("btn-toggle-key");
      if (toggleKeyBtn && this.apiKeyInput) {
        toggleKeyBtn.addEventListener("click", function () {
          self.apiKeyInput.type = (self.apiKeyInput.type === "password") ? "text" : "password";
        });
      }

      // Drawer Connection Live Test
      if (this.drawerTestBtn) {
        this.drawerTestBtn.addEventListener("click", function () {
          self.testDrawerConnection();
        });
      }

      // Drawer Form Submit validation
      if (this.drawerForm) {
        this.drawerForm.addEventListener("submit", function (e) {
          if (self.modelNameHidden && !self.modelNameHidden.value.trim()) {
            e.preventDefault();
            alert("Please select or add at least one Model.");
            if (self.modelTrigger) self.modelTrigger.click();
          }
        });
      }
    },

    selectPresetCard: function (presetKey) {
      var config = PRESET_CONFIGS[presetKey] || PRESET_CONFIGS.custom;
      document.querySelectorAll(".settings-choice").forEach(function (card) {
        var radio = card.querySelector("input[type='radio']");
        if (card.getAttribute("data-preset-key") === presetKey) {
          card.classList.add("settings-choice--active");
          if (radio) radio.checked = true;
        } else {
          card.classList.remove("settings-choice--active");
          if (radio) radio.checked = false;
        }
      });

      if (config.name && presetKey !== "custom" && !this.currentEditId && this.providerNameInput) {
        this.providerNameInput.value = config.name;
      }
      if (config.provider_type && this.providerTypeSelect) {
        this.providerTypeSelect.value = config.provider_type;
      }
      if (config.base_url !== null && config.base_url !== "" && this.baseUrlInput) {
        this.baseUrlInput.value = config.base_url;
      }

      if (config.models && config.models.length > 0) {
        this.setDropdownOptions(config.models, config.default_model);
      }
    },

    setDropdownOptions: function (modelsArray, selectedValue) {
      var self = this;
      this.allKnownModels = [];
      modelsArray.forEach(function (m) {
        var str = String(m).trim();
        if (str && !self.allKnownModels.includes(str)) self.allKnownModels.push(str);
      });

      if (this.enabledModels.length === 0) {
        this.enabledModels = this.allKnownModels.slice(0, 3);
      }

      if (selectedValue) {
        this.defaultModel = selectedValue;
        if (!this.enabledModels.includes(selectedValue)) this.enabledModels.push(selectedValue);
      } else if (this.enabledModels.length === 0 && this.allKnownModels.length > 0) {
        this.defaultModel = this.allKnownModels[0];
        this.enabledModels = [this.allKnownModels[0]];
      }

      this.updateModelDropdownUI();
    },

    updateModelDropdownUI: function () {
      var self = this;
      if (this.modelNameHidden) this.modelNameHidden.value = this.defaultModel;
      if (this.modelsListHidden) this.modelsListHidden.value = this.enabledModels.join(", ");

      if (this.modelTriggerText) {
        if (!this.defaultModel && this.enabledModels.length === 0) {
          this.modelTriggerText.textContent = "-- Enter API key to load models --";
        } else {
          var count = this.enabledModels.length;
          if (count > 1) {
            this.modelTriggerText.textContent = (this.defaultModel || this.enabledModels[0]) + " (" + count + " models enabled)";
          } else {
            this.modelTriggerText.textContent = this.defaultModel || (this.enabledModels[0] || "-- Select Model --");
          }
        }
      }

      if (!this.modelsListItems) return;
      this.modelsListItems.innerHTML = "";

      var filterText = (this.filterModelInput ? this.filterModelInput.value.trim().toLowerCase() : "");
      var filtered = this.allKnownModels.filter(function (m) {
        return !filterText || m.toLowerCase().indexOf(filterText) !== -1;
      });

      if (filtered.length === 0) {
        var emptyEl = document.createElement("div");
        emptyEl.className = "text-muted";
        emptyEl.style.cssText = "font-size: 11px; padding: 8px 10px;";
        emptyEl.textContent = filterText ? "No match for '" + filterText + "'. Click '+ Add' to create it." : "No models loaded.";
        this.modelsListItems.appendChild(emptyEl);
        return;
      }

      filtered.forEach(function (m) {
        var isEnabled = self.enabledModels.includes(m);
        var isDefault = (m === self.defaultModel);

        var row = document.createElement("div");
        row.style.cssText = "display: flex; align-items: center; justify-content: space-between; padding: 5px 8px; border-radius: 4px; gap: 8px; transition: background 0.1s;";
        if (isDefault) {
          row.style.background = "rgba(var(--color-accent-rgb, 99, 102, 241), 0.1)";
        }

        var checkLabel = document.createElement("label");
        checkLabel.className = "form-check m-0";
        checkLabel.style.cssText = "flex: 1; min-width: 0; cursor: pointer; gap: 8px;";

        var checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "form-check-input";
        checkbox.checked = isEnabled;
        checkbox.addEventListener("change", function (e) {
          e.stopPropagation();
          if (this.checked) {
            if (!self.enabledModels.includes(m)) self.enabledModels.push(m);
            if (!self.defaultModel) self.defaultModel = m;
          } else {
            self.enabledModels = self.enabledModels.filter(function (x) { return x !== m; });
            if (self.defaultModel === m) {
              self.defaultModel = self.enabledModels.length > 0 ? self.enabledModels[0] : "";
            }
          }
          self.updateModelDropdownUI();
        });

        var nameSpan = document.createElement("span");
        nameSpan.className = "font-mono text-xs text-truncate";
        nameSpan.style.fontWeight = isDefault ? "700" : "500";
        nameSpan.textContent = m;

        checkLabel.appendChild(checkbox);
        checkLabel.appendChild(nameSpan);
        row.appendChild(checkLabel);

        var defaultBtn = document.createElement("button");
        defaultBtn.type = "button";
        defaultBtn.className = isDefault ? "badge badge--ok" : "badge badge--neutral";
        defaultBtn.style.cssText = "cursor: pointer; border: none; font-size: 10px; padding: 2px 6px;";
        defaultBtn.textContent = isDefault ? "★ Default" : "Set Default";
        defaultBtn.title = isDefault ? "Active default model" : "Make this the default model";

        defaultBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          self.defaultModel = m;
          if (!self.enabledModels.includes(m)) {
            self.enabledModels.push(m);
          }
          self.updateModelDropdownUI();
        });

        row.appendChild(defaultBtn);
        self.modelsListItems.appendChild(row);
      });
    },

    autoFetchModels: function (force) {
      var self = this;
      var apiKey = this.apiKeyInput ? this.apiKeyInput.value.trim() : "";
      var baseUrl = this.baseUrlInput ? this.baseUrlInput.value.trim() : "";
      var providerType = this.providerTypeSelect ? this.providerTypeSelect.value : "openai_compatible";

      if (!apiKey && !this.currentEditId) {
        return;
      }

      if (!force && apiKey === this.lastFetchedKey && apiKey !== "") {
        return;
      }
      this.lastFetchedKey = apiKey;

      if (this.modelStatus) {
        this.modelStatus.style.display = "inline-flex";
        this.modelStatus.className = "badge badge--neutral";
        this.modelStatus.innerHTML = '<span class="spinner-sm"></span> Loading models...';
      }

      var payload = {
        provider_type: providerType,
        api_key: apiKey || undefined,
        base_url: baseUrl,
        provider_id: this.currentEditId || undefined,
      };

      var csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";

      fetch("/plugins/ai_helper/api/fetch-models", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify(payload),
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data.success && data.models && data.models.length > 0) {
            self.setDropdownOptions(data.models, self.defaultModel || (self.modelNameHidden ? self.modelNameHidden.value : ""));
            if (self.modelStatus) {
              self.modelStatus.className = "badge badge--ok";
              self.modelStatus.innerHTML = "✓ " + data.count + " models loaded";
              self.modelStatus.style.display = "inline-flex";
            }
          } else {
            if (self.modelStatus) {
              self.modelStatus.className = "badge badge--error";
              self.modelStatus.innerHTML = data.error || "Failed to load models";
              self.modelStatus.style.display = "inline-flex";
            }
          }
        })
        .catch(function () {
          if (self.modelStatus) self.modelStatus.style.display = "none";
        });
    },

    testDrawerConnection: function () {
      var self = this;
      var origHtml = this.drawerTestBtn.innerHTML;
      this.drawerTestBtn.disabled = true;
      this.drawerTestBtn.innerHTML = '<span class="spinner-sm"></span> Testing...';

      if (this.testIndicator) {
        this.testIndicator.innerHTML = '<span class="badge badge--neutral"><span class="spinner-sm"></span> Connecting...</span>';
      }

      var csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";
      var payload = {
        provider_type: this.providerTypeSelect ? this.providerTypeSelect.value : "openai_compatible",
        api_key: this.apiKeyInput ? this.apiKeyInput.value.trim() : "",
        base_url: this.baseUrlInput ? this.baseUrlInput.value.trim() : "",
        model_name: this.defaultModel || (this.modelNameHidden ? this.modelNameHidden.value.trim() : "gpt-4o-mini"),
        provider_id: this.currentEditId || undefined,
      };

      fetch("/plugins/ai_helper/api/test-connection", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify(payload),
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          self.drawerTestBtn.disabled = false;
          self.drawerTestBtn.innerHTML = origHtml;
          if (typeof lucide !== "undefined") lucide.createIcons();

          if (data.success) {
            if (self.testIndicator) self.testIndicator.innerHTML = '<span class="badge badge--ok">✓ Connected (' + data.latency_ms + 'ms)</span>';
          } else {
            if (self.testIndicator) self.testIndicator.innerHTML = '<span class="badge badge--error">✗ ' + (data.error || "Failed") + '</span>';
          }
        })
        .catch(function (err) {
          self.drawerTestBtn.disabled = false;
          self.drawerTestBtn.innerHTML = origHtml;
          if (typeof lucide !== "undefined") lucide.createIcons();
          if (self.testIndicator) self.testIndicator.innerHTML = '<span class="badge badge--error">✗ Error: ' + err.message + '</span>';
        });
    },

    openAddDrawer: function () {
      this.currentEditId = null;
      if (this.drawerTitle) this.drawerTitle.textContent = "Add AI Provider";
      if (this.drawerSaveBtn) this.drawerSaveBtn.innerHTML = '<i data-lucide="plus"></i> Add Provider';
      if (this.drawerForm) this.drawerForm.action = "/plugins/ai_helper/create";

      if (this.apiKeyInput) this.apiKeyInput.value = "";
      if (this.providerNameInput) this.providerNameInput.value = "";
      if (this.baseUrlInput) this.baseUrlInput.value = "https://api.openai.com/v1";
      if (this.tempInput) this.tempInput.value = "0.2";
      if (this.tokensInput) this.tokensInput.value = "4096";
      if (this.rulesInput) this.rulesInput.value = "";
      if (this.isDefaultCheck) this.isDefaultCheck.checked = false;
      if (this.testIndicator) this.testIndicator.innerHTML = "";
      if (this.modelStatus) this.modelStatus.style.display = "none";

      this.enabledModels = [];
      this.defaultModel = "";
      this.selectPresetCard("openai");

      if (this.drawerModal) this.drawerModal.classList.add("open");
      if (typeof lucide !== "undefined") lucide.createIcons();
    },

    openEditDrawer: function (providerId) {
      this.currentEditId = providerId;
      var row = document.getElementById("provider-row-" + providerId);
      if (!row) return;

      if (this.drawerTitle) this.drawerTitle.textContent = "Edit AI Provider";
      if (this.drawerSaveBtn) this.drawerSaveBtn.innerHTML = '<i data-lucide="check"></i> Save Changes';
      if (this.drawerForm) this.drawerForm.action = "/plugins/ai_helper/" + providerId + "/edit";

      if (this.providerNameInput) this.providerNameInput.value = row.getAttribute("data-name") || "";
      if (this.baseUrlInput) this.baseUrlInput.value = row.getAttribute("data-url") || "";
      if (this.providerTypeSelect) this.providerTypeSelect.value = row.getAttribute("data-type") || "openai_compatible";
      if (this.tempInput) this.tempInput.value = row.getAttribute("data-temp") || "0.2";
      if (this.tokensInput) this.tokensInput.value = row.getAttribute("data-tokens") || "4096";
      if (this.rulesInput) this.rulesInput.value = row.getAttribute("data-rules") || "";
      if (this.isDefaultCheck) this.isDefaultCheck.checked = (row.getAttribute("data-default") === "1");
      if (this.testIndicator) this.testIndicator.innerHTML = "";
      if (this.modelStatus) this.modelStatus.style.display = "none";

      var mName = row.getAttribute("data-model") || "";
      var mListStr = row.getAttribute("data-models") || "";
      var mList = mListStr ? mListStr.split(",").map(function (s) { return s.trim(); }).filter(Boolean) : [];
      if (mName && !mList.includes(mName)) mList.unshift(mName);

      this.enabledModels = mList.slice();
      this.defaultModel = mName || (mList.length > 0 ? mList[0] : "");
      this.setDropdownOptions(mList, this.defaultModel);

      var matchedPreset = "custom";
      var pUrl = row.getAttribute("data-url") || "";
      for (var key in PRESET_CONFIGS) {
        if (key !== "custom" && PRESET_CONFIGS[key].base_url === pUrl) {
          matchedPreset = key;
          break;
        }
      }
      this.selectPresetCard(matchedPreset);

      if (this.drawerModal) this.drawerModal.classList.add("open");
      if (typeof lucide !== "undefined") lucide.createIcons();
    },

    closeDrawer: function () {
      if (this.drawerModal) this.drawerModal.classList.remove("open");
      this.currentEditId = null;
    },

    checkUrlParams: function () {
      if (window.location.search.indexOf("open=create") !== -1) {
        this.openAddDrawer();
      }
    },
  };

  // Expose globally
  window.AiHelperAdmin = AiHelperAdmin;
  window.openAddDrawer = function () { AiHelperAdmin.openAddDrawer(); };
  window.openEditDrawer = function (id) { AiHelperAdmin.openEditDrawer(id); };
  window.closeDrawer = function () { AiHelperAdmin.closeDrawer(); };

  // Row Action Helpers
  window.testProvider = function (providerId, btn) {
    var origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = "...";

    var csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";

    fetch("/plugins/ai_helper/" + providerId + "/test", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        btn.disabled = false;
        btn.innerHTML = origHtml;
        if (typeof lucide !== "undefined") lucide.createIcons();

        if (data.success) {
          alert("Connection successful (" + data.latency_ms + "ms)");
          window.location.reload();
        } else {
          alert("Connection failed: " + (data.error || "Unknown error"));
        }
      })
      .catch(function (err) {
        btn.disabled = false;
        btn.innerHTML = origHtml;
        if (typeof lucide !== "undefined") lucide.createIcons();
        alert("Network error: " + err.message);
      });
  };

  window.setDefaultProvider = function (providerId) {
    var csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";

    fetch("/plugins/ai_helper/" + providerId + "/set-default", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.status === "ok") {
          window.location.reload();
        } else {
          alert("Failed to set default: " + (data.message || "Unknown error"));
        }
      })
      .catch(function (err) {
        alert("Error: " + err.message);
      });
  };

  window.deleteProvider = function (providerId, providerName) {
    if (!confirm("Are you sure you want to delete '" + providerName + "'?")) {
      return;
    }

    var csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";

    fetch("/plugins/ai_helper/" + providerId, {
      method: "DELETE",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.status === "ok") {
          window.location.reload();
        } else {
          alert("Failed to delete provider: " + (data.message || "Unknown error"));
        }
      })
      .catch(function (err) {
        alert("Error deleting provider: " + err.message);
      });
  };

  window.toggleSelectiveFields = function () {
    var selContainer = document.getElementById("selective-scope-container");
    if (!selContainer) return;
    var mode = document.querySelector('input[name="global_mode"]:checked')?.value;
    selContainer.style.display = (mode === "selective") ? "block" : "none";
  };

  window.savePermissions = function () {
    var form = document.getElementById("ai-permissions-form");
    if (!form) return;

    var btn = document.getElementById("btn-save-permissions");
    var statusMsg = document.getElementById("permissions-status-msg");
    var csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";

    var globalMode = form.querySelector('input[name="global_mode"]:checked')?.value || "full_read_only";
    var payload = {
      global_mode: globalMode,
      allow_domains_proxy: form.querySelector('input[name="allow_domains_proxy"]')?.checked || false,
      allow_dns: form.querySelector('input[name="allow_dns"]')?.checked || false,
      allow_php_sites: form.querySelector('input[name="allow_php_sites"]')?.checked || false,
      allow_container_apps: form.querySelector('input[name="allow_container_apps"]')?.checked || false,
      allow_databases: form.querySelector('input[name="allow_databases"]')?.checked || false,
      allow_files_read: form.querySelector('input[name="allow_files_read"]')?.checked || false,
      allowed_domains: form.querySelector('input[name="allowed_domains"]')?.value || "[]",
      allowed_app_ids: form.querySelector('input[name="allowed_app_ids"]')?.value || "[]",
    };

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving...';
    }

    fetch("/plugins/ai_helper/api/permissions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(payload),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<i data-lucide="save"></i> Save Permissions';
          if (typeof lucide !== "undefined") lucide.createIcons();
        }

        if (statusMsg) {
          if (data.status === "ok") {
            statusMsg.className = "alert alert--ok mt-sm";
            statusMsg.textContent = "Permissions updated successfully!";
            statusMsg.style.display = "block";
            setTimeout(function () { statusMsg.style.display = "none"; }, 3000);
          } else {
            statusMsg.className = "alert alert--danger mt-sm";
            statusMsg.textContent = "Failed to update permissions: " + (data.message || "Unknown error");
            statusMsg.style.display = "block";
          }
        }
      })
      .catch(function (err) {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<i data-lucide="save"></i> Save Permissions';
          if (typeof lucide !== "undefined") lucide.createIcons();
        }
        if (statusMsg) {
          statusMsg.className = "alert alert--danger mt-sm";
          statusMsg.textContent = "Error saving permissions: " + err.message;
          statusMsg.style.display = "block";
        }
      });
  };

  // Initialize when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { AiHelperAdmin.init(); });
  } else {
    AiHelperAdmin.init();
  }
})(window, document);

