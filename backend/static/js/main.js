/**
 * main.js — Global JS: fetch wrapper, toast, modal, shared init
 * All page-specific logic lives in modules/
 */

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
      headers: { "Content-Type": "application/json" },
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
    form.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      data[cb.name] = cb.checked;
    });
    return this.post(form.action, data);
  },
};

/**
 * Submit a browser form POST (server-rendered Form routes).
 * @param {string} action URL
 * @param {Record<string,string|number|boolean>} [fields]
 */
function submitPost(action, fields = {}) {
  const form = document.createElement("form");
  form.method = "POST";
  form.action = action;
  form.style.display = "none";

  Object.entries(fields || {}).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = key;
    input.value = String(value);
    form.appendChild(input);
  });

  document.body.appendChild(form);
  form.submit();
}

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

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove("hidden");
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add("hidden");
}

document.addEventListener("click", (e) => {
  if (e.target.classList.contains("modal-backdrop")) {
    e.target.classList.add("hidden");
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((m) =>
      m.classList.add("hidden")
    );
  }
});

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

document.addEventListener("DOMContentLoaded", () => {
  const path = window.location.pathname;
  document.querySelectorAll(".sidebar__item[data-path]").forEach((item) => {
    const itemPath = item.getAttribute("data-path");
    if (path === itemPath || (itemPath !== "/" && path.startsWith(itemPath))) {
      item.classList.add("sidebar__item--active");
    }
  });
});

window.panel = panel;
window.submitPost = submitPost;
window.toast = toast;
window.openModal = openModal;
window.closeModal = closeModal;
window.confirmAction = confirmAction;
