/**
 * main.js — Global JS: fetch wrapper, toast, modal, shared init
 * All page-specific logic lives in modules/
 */

// ============================================================
// FETCH WRAPPER
// ============================================================
/**
 * panel.post(url, data) — POST JSON, returns parsed response or throws.
 * panel.del(url)        — POST to delete endpoint.
 */
const panel = {
  async post(url, data = {}) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(json.detail || `Request failed (${res.status})`);
    }
    return json;
  },

  async get(url) {
    const res = await fetch(url, { method: "GET" });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(json.detail || `Request failed (${res.status})`);
    }
    return json;
  },

  async postForm(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    // Convert checkbox to boolean
    form.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      data[cb.name] = cb.checked;
    });
    return this.post(form.action, data);
  },
};

// ============================================================
// TOAST
// ============================================================
function toast(message, type = "success") {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }
  const el = document.createElement("div");
  el.className = `toast toast--${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ============================================================
// MODAL
// ============================================================
function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove("hidden");
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add("hidden");
}

// Close modal on backdrop click
document.addEventListener("click", (e) => {
  if (e.target.classList.contains("modal-backdrop")) {
    e.target.classList.add("hidden");
  }
});

// Close modal on Escape key
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((m) =>
      m.classList.add("hidden")
    );
  }
});

// ============================================================
// CONFIRM DIALOG
// ============================================================
/**
 * confirm(message, onConfirm) — show confirm modal then call onConfirm
 * Uses the #confirm-modal in layout.html
 */
function confirmAction(message, onConfirm) {
  const modal = document.getElementById("confirm-modal");
  const msgEl = document.getElementById("confirm-message");
  const okBtn = document.getElementById("confirm-ok");
  if (!modal || !msgEl || !okBtn) return;

  msgEl.textContent = message;
  modal.classList.remove("hidden");

  const handler = async () => {
    okBtn.removeEventListener("click", handler);
    modal.classList.add("hidden");
    await onConfirm();
  };
  okBtn.addEventListener("click", handler);
}

// ============================================================
// GLOBAL INIT
// ============================================================
document.addEventListener("DOMContentLoaded", () => {
  // Highlight active sidebar item based on current path
  const path = window.location.pathname;
  document.querySelectorAll(".sidebar__item[data-path]").forEach((item) => {
    const itemPath = item.getAttribute("data-path");
    if (path === itemPath || (itemPath !== "/" && path.startsWith(itemPath))) {
      item.classList.add("sidebar__item--active");
    }
  });
});

// Export for modules
window.panel = panel;
window.toast = toast;
window.openModal = openModal;
window.closeModal = closeModal;
window.confirmAction = confirmAction;
