/**
 * modules/provider_drawer.js — AI Provider Drawer & CRUD Controller
 */
import { PRESET_CONFIGS } from "./presets.js";

export const ProviderDrawerManager = {
  currentEditId: null,
  allKnownModels: [],
  enabledModels: [],
  defaultModel: "",
  fetchTimeout: null,
  lastFetchedKey: "",

  init() {
    this.cacheElements();
    this.bindEvents();
  },

  cacheElements() {
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

  bindEvents() {
    document.querySelectorAll(".settings-choice").forEach((card) => {
      card.addEventListener("click", () => {
        const key = card.getAttribute("data-preset-key");
        if (key) this.selectPresetCard(key);
        if (this.apiKeyInput && this.apiKeyInput.value.trim().length >= 6) {
          this.autoFetchModels();
        }
      });
    });

    if (this.modelTrigger && this.modelMenu) {
      this.modelTrigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const isOpen = this.modelMenu.style.display === "block";
        this.modelMenu.style.display = isOpen ? "none" : "block";
        if (!isOpen && this.filterModelInput) {
          setTimeout(() => this.filterModelInput.focus(), 50);
        }
      });

      document.addEventListener("click", (e) => {
        if (this.modelDropdownWrap && !this.modelDropdownWrap.contains(e.target)) {
          this.modelMenu.style.display = "none";
        }
      });
      this.modelMenu.addEventListener("click", (e) => e.stopPropagation());
    }

    if (this.filterModelInput) {
      this.filterModelInput.addEventListener("input", () => this.updateModelDropdownUI());
      this.filterModelInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          if (this.btnAddModel) this.btnAddModel.click();
        }
      });
    }

    if (this.btnAddModel && this.filterModelInput) {
      this.btnAddModel.addEventListener("click", () => {
        const val = this.filterModelInput.value.trim();
        if (!val) return;
        if (!this.allKnownModels.includes(val)) this.allKnownModels.unshift(val);
        if (!this.enabledModels.includes(val)) this.enabledModels.push(val);
        if (!this.defaultModel) this.defaultModel = val;
        this.filterModelInput.value = "";
        this.updateModelDropdownUI();
      });
    }

    if (this.btnFetchModels) {
      this.btnFetchModels.addEventListener("click", () => this.autoFetchModels(true));
    }

    if (this.apiKeyInput) {
      this.apiKeyInput.addEventListener("input", () => {
        clearTimeout(this.fetchTimeout);
        if (this.apiKeyInput.value.trim().length >= 6) {
          this.fetchTimeout = setTimeout(() => this.autoFetchModels(), 500);
        }
      });
    }

    const toggleKeyBtn = document.getElementById("drawer-btn-toggle-key") || document.getElementById("btn-toggle-key");
    if (toggleKeyBtn && this.apiKeyInput) {
      toggleKeyBtn.addEventListener("click", () => {
        this.apiKeyInput.type = this.apiKeyInput.type === "password" ? "text" : "password";
      });
    }

    if (this.drawerTestBtn) {
      this.drawerTestBtn.addEventListener("click", () => this.testDrawerConnection());
    }
  },

  selectPresetCard(presetKey) {
    const config = PRESET_CONFIGS[presetKey] || PRESET_CONFIGS.custom;
    document.querySelectorAll(".settings-choice").forEach((card) => {
      const radio = card.querySelector("input[type='radio']");
      const isTarget = card.getAttribute("data-preset-key") === presetKey;
      card.classList.toggle("settings-choice--active", isTarget);
      if (radio) radio.checked = isTarget;
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
  },

  setDropdownOptions(modelsArray, selectedValue) {
    this.allKnownModels = [];
    (modelsArray || []).forEach((m) => {
      const str = String(m).trim();
      if (str && !this.allKnownModels.includes(str)) this.allKnownModels.push(str);
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

  updateModelDropdownUI() {
    if (this.modelNameHidden) this.modelNameHidden.value = this.defaultModel;
    if (this.modelsListHidden) this.modelsListHidden.value = this.enabledModels.join(", ");

    if (this.modelTriggerText) {
      if (!this.defaultModel && this.enabledModels.length === 0) {
        this.modelTriggerText.textContent = "-- Enter API key to load models --";
      } else {
        const count = this.enabledModels.length;
        this.modelTriggerText.textContent = count > 1
          ? `${this.defaultModel || this.enabledModels[0]} (${count} models)`
          : (this.defaultModel || this.enabledModels[0] || "-- Select Model --");
      }
    }

    if (!this.modelsListItems) return;
    this.modelsListItems.innerHTML = "";

    const filterText = (this.filterModelInput ? this.filterModelInput.value.trim().toLowerCase() : "");
    const filtered = this.allKnownModels.filter((m) => !filterText || m.toLowerCase().includes(filterText));

    if (filtered.length === 0) {
      const emptyEl = document.createElement("div");
      emptyEl.className = "text-muted text-xs p-xs";
      emptyEl.textContent = filterText ? `No match for '${filterText}'. Click '+ Add' to create it.` : "No models loaded.";
      this.modelsListItems.appendChild(emptyEl);
      return;
    }

    filtered.forEach((m) => {
      const isEnabled = this.enabledModels.includes(m);
      const isDefault = m === this.defaultModel;

      const row = document.createElement("div");
      row.className = `model-dropdown-item${isDefault ? " model-dropdown-item--default" : ""}`;

      const checkLabel = document.createElement("label");
      checkLabel.className = "form-check m-0";
      checkLabel.style.cssText = "flex: 1; cursor: pointer; gap: 8px; color: var(--color-text);";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "form-check-input";
      checkbox.checked = isEnabled;
      checkbox.addEventListener("change", (e) => {
        e.stopPropagation();
        if (checkbox.checked) {
          if (!this.enabledModels.includes(m)) this.enabledModels.push(m);
          if (!this.defaultModel) this.defaultModel = m;
        } else {
          this.enabledModels = this.enabledModels.filter((x) => x !== m);
          if (this.defaultModel === m) {
            this.defaultModel = this.enabledModels.length > 0 ? this.enabledModels[0] : "";
          }
        }
        this.updateModelDropdownUI();
      });

      const nameSpan = document.createElement("span");
      nameSpan.className = "font-mono text-xs";
      nameSpan.style.cssText = "color: var(--color-text); font-weight: " + (isDefault ? "700" : "500") + ";";
      nameSpan.textContent = m;

      checkLabel.appendChild(checkbox);
      checkLabel.appendChild(nameSpan);
      row.appendChild(checkLabel);

      const defaultBtn = document.createElement("button");
      defaultBtn.type = "button";
      defaultBtn.className = isDefault ? "badge badge--ok" : "badge badge--neutral";
      defaultBtn.style.cssText = "cursor: pointer; border: none; font-size: 10px; padding: 2px 6px;";
      defaultBtn.textContent = isDefault ? "★ Default" : "Set Default";
      defaultBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this.defaultModel = m;
        if (!this.enabledModels.includes(m)) this.enabledModels.push(m);
        this.updateModelDropdownUI();
      });

      row.appendChild(defaultBtn);
      this.modelsListItems.appendChild(row);
    });
  },

  autoFetchModels(force) {
    const apiKey = this.apiKeyInput ? this.apiKeyInput.value.trim() : "";
    const baseUrl = this.baseUrlInput ? this.baseUrlInput.value.trim() : "";
    const providerType = this.providerTypeSelect ? this.providerTypeSelect.value : "openai_compatible";

    if (!apiKey && !this.currentEditId) return;
    if (!force && apiKey === this.lastFetchedKey && apiKey !== "") return;
    this.lastFetchedKey = apiKey;

    if (this.modelStatus) {
      this.modelStatus.style.display = "inline-flex";
      this.modelStatus.className = "badge badge--neutral";
      this.modelStatus.innerHTML = '<span class="spinner-sm"></span> Loading models...';
    }

    const payload = {
      provider_type: providerType,
      api_key: apiKey || undefined,
      base_url: baseUrl,
      provider_id: this.currentEditId || undefined,
    };
    const csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";

    fetch("/plugins/ai_helper/api/fetch-models", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(payload),
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.success && data.models && data.models.length > 0) {
          this.setDropdownOptions(data.models, this.defaultModel || (this.modelNameHidden ? this.modelNameHidden.value : ""));
          if (this.modelStatus) {
            this.modelStatus.className = "badge badge--ok";
            this.modelStatus.innerHTML = `✓ ${data.count} models loaded`;
            this.modelStatus.style.display = "inline-flex";
          }
        } else if (this.modelStatus) {
          this.modelStatus.className = "badge badge--error";
          this.modelStatus.innerHTML = data.error || "Failed to load models";
          this.modelStatus.style.display = "inline-flex";
        }
      })
      .catch(() => {
        if (this.modelStatus) this.modelStatus.style.display = "none";
      });
  },

  testDrawerConnection() {
    const origHtml = this.drawerTestBtn.innerHTML;
    this.drawerTestBtn.disabled = true;
    this.drawerTestBtn.innerHTML = '<span class="spinner-sm"></span> Testing...';
    if (this.testIndicator) {
      this.testIndicator.innerHTML = '<span class="badge badge--neutral"><span class="spinner-sm"></span> Connecting...</span>';
    }

    const csrfToken = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";
    const payload = {
      provider_type: this.providerTypeSelect ? this.providerTypeSelect.value : "openai_compatible",
      api_key: this.apiKeyInput ? this.apiKeyInput.value.trim() : "",
      base_url: this.baseUrlInput ? this.baseUrlInput.value.trim() : "",
      model_name: this.defaultModel || (this.modelNameHidden ? this.modelNameHidden.value.trim() : ""),
      provider_id: this.currentEditId || undefined,
    };

    fetch("/plugins/ai_helper/api/test-connection", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(payload),
    })
      .then((res) => res.json())
      .then((data) => {
        this.drawerTestBtn.disabled = false;
        this.drawerTestBtn.innerHTML = origHtml;
        if (typeof lucide !== "undefined") lucide.createIcons();
        if (this.testIndicator) {
          this.testIndicator.innerHTML = data.success
            ? `<span class="badge badge--ok">✓ Connected (${data.latency_ms}ms)</span>`
            : `<span class="badge badge--error">✗ ${data.error || "Failed"}</span>`;
        }
      })
      .catch((err) => {
        this.drawerTestBtn.disabled = false;
        this.drawerTestBtn.innerHTML = origHtml;
        if (typeof lucide !== "undefined") lucide.createIcons();
        if (this.testIndicator) {
          this.testIndicator.innerHTML = `<span class="badge badge--error">✗ Error: ${err.message}</span>`;
        }
      });
  },

  openAddDrawer() {
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

    if (this.drawerModal) {
      this.drawerModal.classList.remove("hidden");
      document.body.classList.add("modal-open");
    }
    if (typeof lucide !== "undefined") lucide.createIcons();
  },

  openEditDrawer(providerId) {
    this.currentEditId = providerId;
    const row = document.getElementById("provider-row-" + providerId);
    if (!row) return;

    if (this.drawerTitle) this.drawerTitle.textContent = "Edit AI Provider";
    if (this.drawerSaveBtn) this.drawerSaveBtn.innerHTML = '<i data-lucide="check"></i> Save Changes';
    if (this.drawerForm) this.drawerForm.action = `/plugins/ai_helper/${providerId}/edit`;

    if (this.providerNameInput) this.providerNameInput.value = row.getAttribute("data-name") || "";
    if (this.baseUrlInput) this.baseUrlInput.value = row.getAttribute("data-url") || "";
    if (this.providerTypeSelect) this.providerTypeSelect.value = row.getAttribute("data-type") || "openai_compatible";
    if (this.tempInput) this.tempInput.value = row.getAttribute("data-temp") || "0.2";
    if (this.tokensInput) this.tokensInput.value = row.getAttribute("data-tokens") || "4096";
    if (this.rulesInput) this.rulesInput.value = row.getAttribute("data-rules") || "";
    if (this.isDefaultCheck) this.isDefaultCheck.checked = row.getAttribute("data-default") === "1";
    if (this.testIndicator) this.testIndicator.innerHTML = "";
    if (this.modelStatus) this.modelStatus.style.display = "none";

    const mName = row.getAttribute("data-model") || "";
    const mListStr = row.getAttribute("data-models") || "";
    const mList = mListStr ? mListStr.split(",").map((s) => s.trim()).filter(Boolean) : [];
    if (mName && !mList.includes(mName)) mList.unshift(mName);

    this.enabledModels = mList.slice();
    this.defaultModel = mName || (mList.length > 0 ? mList[0] : "");
    this.setDropdownOptions(mList, this.defaultModel);

    let matchedPreset = "custom";
    const pUrl = row.getAttribute("data-url") || "";
    for (const key in PRESET_CONFIGS) {
      if (key !== "custom" && PRESET_CONFIGS[key].base_url === pUrl) {
        matchedPreset = key;
        break;
      }
    }
    this.selectPresetCard(matchedPreset);

    if (this.drawerModal) {
      this.drawerModal.classList.remove("hidden");
      document.body.classList.add("modal-open");
    }
    if (typeof lucide !== "undefined") lucide.createIcons();
  },

  closeDrawer() {
    if (this.drawerModal) {
      this.drawerModal.classList.add("hidden");
      document.body.classList.remove("modal-open");
    }
    this.currentEditId = null;
  },
};
