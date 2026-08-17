/**
 * modules/multiselect.js — Reusable Searchable Multi-Select Tag Dropdown Component
 */

export class MultiSelectPicker {
  constructor(options) {
    this.key = options.key;
    this.container = document.getElementById("ms-" + this.key);
    this.hiddenInput = document.getElementById("input_allowed_" + this.key);
    this.tagsContainer = document.getElementById("ms-tags-" + this.key);
    this.placeholder = document.getElementById("ms-placeholder-" + this.key);
    this.listContainer = document.getElementById("ms-list-" + this.key);
    this.countBadge = document.getElementById("count-badge-" + this.key);
    this.allOptions = [];
    this.selected = [];

    if (this.container) {
      this.init();
    }
  }

  init() {
    this.trigger = this.container.querySelector(".perm-multiselect__trigger");
    this.dropdown = this.container.querySelector(".perm-multiselect__dropdown");
    this.searchInput = this.container.querySelector(".perm-multiselect__search");
    this.addCustomBtn = this.container.querySelector(".btn-add-custom-tag");
    this.selectAllBtn = this.container.querySelector(".btn-select-all");
    this.clearAllBtn = this.container.querySelector(".btn-clear-all");

    const initialVal = this.hiddenInput ? this.hiddenInput.value.trim() : "[]";
    try {
      if (initialVal.startsWith("[")) {
        this.selected = JSON.parse(initialVal) || [];
      } else if (initialVal) {
        this.selected = initialVal.split(",").map((x) => x.trim()).filter(Boolean);
      }
    } catch {
      this.selected = [];
    }

    if (this.trigger) {
      this.trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const isOpen = this.container.classList.contains("open");
        document.querySelectorAll(".perm-multiselect.open").forEach((el) => {
          if (el !== this.container) el.classList.remove("open");
        });
        this.container.classList.toggle("open", !isOpen);
        if (!isOpen && this.searchInput) {
          setTimeout(() => this.searchInput.focus(), 50);
        }
      });
    }

    if (this.dropdown) {
      this.dropdown.addEventListener("click", (e) => e.stopPropagation());
    }

    if (this.searchInput) {
      this.searchInput.addEventListener("input", () => this.renderList());
      this.searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          this.addCustomFromSearch();
        }
      });
    }

    if (this.addCustomBtn) {
      this.addCustomBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this.addCustomFromSearch();
      });
    }

    if (this.selectAllBtn) {
      this.selectAllBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this.allOptions.forEach((opt) => {
          const val = String(opt.name || opt.id || opt);
          if (!this.selected.includes(val)) this.selected.push(val);
        });
        this.updateUI();
      });
    }

    if (this.clearAllBtn) {
      this.clearAllBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this.selected = [];
        this.updateUI();
      });
    }

    this.updateUI();
  }

  setOptions(optionsArray) {
    this.allOptions = optionsArray || [];
    this.renderList();
  }

  addCustomFromSearch() {
    if (!this.searchInput) return;
    const val = this.searchInput.value.trim();
    if (!val) return;
    if (!this.selected.includes(val)) this.selected.push(val);
    this.searchInput.value = "";
    this.updateUI();
  }

  toggleItem(val) {
    const idx = this.selected.indexOf(val);
    if (idx !== -1) {
      this.selected.splice(idx, 1);
    } else {
      this.selected.push(val);
    }
    this.updateUI();
  }

  removeItem(val) {
    const idx = this.selected.indexOf(val);
    if (idx !== -1) {
      this.selected.splice(idx, 1);
      this.updateUI();
    }
  }

  updateUI() {
    if (this.hiddenInput) {
      this.hiddenInput.value = JSON.stringify(this.selected);
    }

    if (this.countBadge) {
      const count = this.selected.length;
      this.countBadge.textContent = count === 0 ? "0 selected" : `${count} selected`;
      this.countBadge.className = count > 0 ? "badge badge--ok text-xs" : "text-xs text-muted";
    }

    if (this.tagsContainer) {
      this.tagsContainer.innerHTML = "";
      if (this.selected.length === 0) {
        if (this.placeholder) this.placeholder.style.display = "inline";
      } else {
        if (this.placeholder) this.placeholder.style.display = "none";
        this.selected.forEach((val) => {
          const tag = document.createElement("span");
          tag.className = "perm-tag";
          tag.textContent = val;

          const removeBtn = document.createElement("span");
          removeBtn.className = "perm-tag__remove";
          removeBtn.innerHTML = "✕";
          removeBtn.title = `Remove ${val}`;
          removeBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            this.removeItem(val);
          });

          tag.appendChild(removeBtn);
          this.tagsContainer.appendChild(tag);
        });
      }
    }

    this.renderList();
  }

  renderList() {
    if (!this.listContainer) return;
    this.listContainer.innerHTML = "";

    const filterText = (this.searchInput ? this.searchInput.value.trim().toLowerCase() : "");
    const visible = this.allOptions.filter((opt) => {
      const name = (opt.name || opt.domain || opt.id || String(opt)).toLowerCase();
      const extra = (opt.type || opt.engine || opt.project_type || opt.preset || "").toLowerCase();
      return !filterText || name.includes(filterText) || extra.includes(filterText);
    });

    if (visible.length === 0) {
      const empty = document.createElement("div");
      empty.className = "text-muted text-xs p-xs text-center";
      empty.textContent = filterText ? `No match for '${filterText}'. Click '+ Add' to include custom.` : "No items found.";
      this.listContainer.appendChild(empty);
      return;
    }

    visible.forEach((opt) => {
      const val = String(this.key === "databases" || this.key === "domains" ? (opt.name || opt) : (opt.id || opt.name || opt));
      const isChecked = this.selected.includes(val);

      const row = document.createElement("div");
      row.className = `perm-multiselect__item${isChecked ? " perm-multiselect__item--selected" : ""}`;

      const label = document.createElement("label");
      label.className = "form-check m-0";
      label.style.cssText = "flex: 1; cursor: pointer; gap: 8px;";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "form-check-input";
      checkbox.checked = isChecked;
      checkbox.addEventListener("change", (e) => {
        e.stopPropagation();
        this.toggleItem(val);
      });

      const nameSpan = document.createElement("span");
      nameSpan.className = "font-mono text-xs";
      nameSpan.style.fontWeight = isChecked ? "700" : "500";
      nameSpan.textContent = val;

      label.appendChild(checkbox);
      label.appendChild(nameSpan);
      row.appendChild(label);

      const metaType = opt.type || opt.engine || opt.project_type || opt.preset;
      if (metaType) {
        const badge = document.createElement("span");
        badge.className = "badge badge--neutral";
        badge.style.cssText = "font-size: 9px; padding: 1px 4px; text-transform: uppercase;";
        badge.textContent = metaType;
        row.appendChild(badge);
      }

      row.addEventListener("click", (e) => {
        if (e.target !== checkbox) this.toggleItem(val);
      });

      this.listContainer.appendChild(row);
    });
  }
}
