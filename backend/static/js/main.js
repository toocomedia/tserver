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
function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return (meta && meta.getAttribute("content")) || "";
}

function withCsrfHeaders(headers = {}) {
  const token = csrfToken();
  const next = { ...headers };
  if (token) next["X-CSRF-Token"] = token;
  return next;
}

function formatDetail(detail) {
  if (!detail) return "Request failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  }
  return String(detail);
}

const panel = {
  async post(url, data = {}) {
    const res = await fetch(url, {
      method: "POST",
      headers: withCsrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(data),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(formatDetail(json.detail) || `Request failed (${res.status})`);
    }
    return json;
  },

  async get(url) {
    const res = await fetch(url, { method: "GET" });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(formatDetail(json.detail) || `Request failed (${res.status})`);
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
 * confirmAction(message, onConfirm) — show confirm modal then call onConfirm.
 * Uses #confirm-modal in layout.html. Always clears prior OK handlers
 * (clone button) so Cancel + re-open does not stack listeners.
 */
function confirmAction(message, onConfirm) {
  const modal = document.getElementById("confirm-modal");
  const msgEl = document.getElementById("confirm-message");
  let okBtn = document.getElementById("confirm-ok");

  if (!modal || !msgEl || !okBtn) {
    if (window.confirm(message)) {
      Promise.resolve(onConfirm()).catch((err) => {
        console.error(err);
        toast(err.message || "Action failed", "danger");
      });
    }
    return;
  }

  msgEl.textContent = message;

  // Drop any previous click handlers on OK
  const freshOk = okBtn.cloneNode(true);
  okBtn.parentNode.replaceChild(freshOk, okBtn);
  okBtn = freshOk;

  modal.classList.remove("hidden");

  const close = () => {
    modal.classList.add("hidden");
  };

  const onOk = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    okBtn.removeEventListener("click", onOk);
    close();
    try {
      await onConfirm();
    } catch (err) {
      console.error(err);
      toast(err.message || "Action failed", "danger");
    }
  };

  okBtn.addEventListener("click", onOk, { once: true });
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

  // Inject CSRF into HTML forms (login, logout, domain create, etc.)
  const token = csrfToken();
  if (token) {
    document.querySelectorAll("form").forEach((form) => {
      const method = (form.getAttribute("method") || "get").toLowerCase();
      if (method !== "post") return;
      if (form.querySelector('input[name="csrf_token"]')) return;
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = token;
      form.appendChild(input);
    });
  }
});

// Export for modules
window.panel = panel;
window.csrfToken = csrfToken;
window.toast = toast;
window.openModal = openModal;
window.closeModal = closeModal;
window.confirmAction = confirmAction;
