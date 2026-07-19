/**
 * main.js — Global JS: fetch wrapper, toast, modal, shared init
 * All page-specific logic lives in modules/
 */

/**
 * One URL system for the panel (must match backend/templating.py PATHS).
 * Section indexes end with /. Detail pages: path('domains', id) → /domains/3
 */
const PATHS = {
  home: "/",
  dashboard: "/",
  login: "/login",
  logout: "/logout",
  domains: "/domains/",
  domains_create: "/domains/create",
  proxy: "/proxy/",
  proxy_create: "/proxy/create",
  dns: "/dns/",
  ssl: "/ssl/",
  ssl_issue: "/ssl/issue",
  settings: "/settings/",
  errors: "/admin/errors/",
  health: "/api/health",
  api_settings: "/api/settings",
};

function path(name, ...parts) {
  let base = PATHS[name] || (String(name).startsWith("/") ? name : `/${name}`);
  if (parts.length) {
    const extra = parts
      .filter((p) => p !== undefined && p !== null && String(p) !== "")
      .map((p) => String(p).replace(/^\/+|\/+$/g, ""))
      .join("/");
    const root = base.replace(/\/+$/, "");
    base = extra ? `${root}/${extra}` : base;
  }
  return base;
}

/** Public open URL — always trailing / (same as backend public_url). */
function publicUrl(host, { https = false, port = null } = {}) {
  const h = String(host || "").replace(/\/+$/, "");
  if (!h) return "/";
  const scheme = https ? "https" : "http";
  if (port != null) {
    const p = Number(port);
    if (https && p === 443) return `${scheme}://${h}/`;
    if (!https && p === 80) return `${scheme}://${h}/`;
    return `${scheme}://${h}:${p}/`;
  }
  return `${scheme}://${h}/`;
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

/**
 * confirmAction(message, onConfirm, options?)
 * options: { title, okLabel, danger }
 * - Without options → delete-style (red "Delete") for existing callers
 * - With okLabel / danger:false → primary confirm (e.g. Issue SSL)
 */
function confirmAction(message, onConfirm, options) {
  const opts = options || {};
  const title = opts.title || "Confirm Action";
  const danger = options ? !!opts.danger : true;
  const okLabel = opts.okLabel || (danger ? "Delete" : "Confirm");

  const modal = document.getElementById("confirm-modal");
  const msgEl = document.getElementById("confirm-message");
  const titleEl = document.getElementById("confirm-title");
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

  if (titleEl) titleEl.textContent = title;
  msgEl.textContent = message;
  okBtn.textContent = okLabel;
  okBtn.className = danger ? "btn btn--danger" : "btn btn--primary";

  const freshOk = okBtn.cloneNode(true);
  okBtn.parentNode.replaceChild(freshOk, okBtn);
  okBtn = freshOk;
  okBtn.textContent = okLabel;
  okBtn.className = danger ? "btn btn--danger" : "btn btn--primary";

  modal.classList.remove("hidden");

  const close = () => {
    modal.classList.add("hidden");
    // restore default for next delete dialogs
    if (titleEl) titleEl.textContent = "Confirm Action";
    okBtn.textContent = "Delete";
    okBtn.className = "btn btn--danger";
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
  const current = window.location.pathname;
  document.querySelectorAll(".sidebar__item[data-path]").forEach((item) => {
    const itemPath = item.getAttribute("data-path") || "";
    // Normalize: /domains and /domains/ both match
    const a = current.replace(/\/+$/, "") || "/";
    const b = itemPath.replace(/\/+$/, "") || "/";
    if (a === b || (b !== "/" && a.startsWith(b + "/"))) {
      item.classList.add("sidebar__item--active");
    }
  });
});

window.PATHS = PATHS;
window.path = path;
window.publicUrl = publicUrl;
window.panel = panel;
window.submitPost = submitPost;
window.toast = toast;
window.openModal = openModal;
window.closeModal = closeModal;
window.confirmAction = confirmAction;
