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
  usage: "/usage",
  health: "/api/health",
  api_settings: "/api/settings",
  api_settings_performance: "/api/settings/performance",
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

/** CSRF token from layout / login meta tag. */
function getCsrfToken() {
  const m = document.querySelector('meta[name="csrf-token"]');
  return m ? (m.getAttribute("content") || "") : "";
}

function csrfHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getCsrfToken();
  if (token) headers["X-CSRF-Token"] = token;
  return headers;
}

function reportSwapWarning(response) {
  if (response?.ok === true && response.swap_warning) {
    toast(response.swap_warning, "warning");
  }
}

const panel = {
  async post(url, data = {}) {
    const res = await fetch(url, {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(data),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(formatDetail(json.detail) || `Request failed (${res.status})`);
    }
    reportSwapWarning(json);
    return json;
  },

  async get(url) {
    const res = await fetch(url, { method: "GET" });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(formatDetail(json.detail) || `Request failed (${res.status})`);
    }
    reportSwapWarning(json);
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

  const payload = { ...(fields || {}) };
  if (!payload.csrf_token) {
    const token = getCsrfToken();
    if (token) payload.csrf_token = token;
  }

  Object.entries(payload).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = key;
    input.value = String(value);
    form.appendChild(input);
  });

  document.body.appendChild(form);
  
  if (typeof window.pjaxNavigate === "function") {
    const formData = new FormData(form);
    window.pjaxNavigate(action, { method: "POST", body: formData });
    form.remove();
  } else {
    form.submit();
  }
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

/**
 * Skeleton helpers — shared for all pages.
 * hideSkeleton(id): fades out a .skeleton-overlay after the first successful load.
 *   A short delay lets real content render before the fade starts.
 * showSkeleton(id): re-shows an overlay (e.g. for a full page reload).
 */
function hideSkeleton(id, delay = 1000) {
  const el = document.getElementById(id);
  if (!el || el.classList.contains("is-hidden")) return Promise.resolve();
  return new Promise((resolve) => {
    setTimeout(() => {
      el.classList.add("is-hidden");
      setTimeout(() => {
        if (el.classList.contains("is-hidden")) {
          el.style.display = "none";
        }
        resolve();
      }, 400);
    }, delay);
  });
}

window.hideSkeleton = hideSkeleton;

function showSkeleton(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove("is-hidden");
}
window.showSkeleton = showSkeleton;

function showGlobalLoader(message = "Loading...") {
  const loader = document.getElementById("global-loader");
  const textEl = document.getElementById("global-loader-text");
  if (loader) {
    if (textEl) textEl.textContent = message;
    loader.classList.remove("hidden");
  }
}
window.showGlobalLoader = showGlobalLoader;

function hideGlobalLoader() {
  const loader = document.getElementById("global-loader");
  if (loader) {
    loader.classList.add("hidden");
  }
}
window.hideGlobalLoader = hideGlobalLoader;

document.addEventListener("click", (e) => {
  if (e.target.classList.contains("modal-backdrop")) {
    if (e.target.dataset.noBackdropClose === "true") return;
    e.target.classList.add("hidden");
  }
});

document.addEventListener("submit", (e) => {
  const button = e.submitter;
  if (!button || button.dataset.noLoading === "true") return;
  button.classList.add("is-loading");
  button.setAttribute("aria-busy", "true");
  button.disabled = true;
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

/**
 * One submit per form: block double-clicks / repeated POSTs.
 * Skips forms marked data-no-disable-submit.
 */
function guardFormSubmitButtons() {
  document.addEventListener(
    "submit",
    (e) => {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.hasAttribute("data-no-disable-submit")) return;
      if (form.getAttribute("data-locked") === "1") {
        e.preventDefault();
        return;
      }
      // Second+ submit must be cancelled (disabled button alone is not enough).
      if (form.getAttribute("data-submitting") === "1") {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      form.setAttribute("data-submitting", "1");
      form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach((btn) => {
        btn.disabled = true;
        btn.setAttribute("aria-disabled", "true");
        if (btn.tagName === "BUTTON" && !btn.dataset.originalLabel) {
          btn.dataset.originalLabel = btn.textContent || "";
          const label = (btn.textContent || "Working").trim();
          if (!label.endsWith("…")) btn.textContent = `${label}…`;
        }
      });
    },
    true
  );
}

document.addEventListener("app:init", () => {
  guardFormSubmitButtons();

  const current = window.location.pathname;
  let bestMatch = null;
  let maxLen = -1;

  document.querySelectorAll(".sidebar__item[data-path]").forEach((item) => {
    item.classList.remove("sidebar__item--active");
    const itemPath = item.getAttribute("data-path") || "";
    // Normalize: /domains and /domains/ both match
    const a = current.replace(/\/+$/, "") || "/";
    const b = itemPath.replace(/\/+$/, "") || "/";
    if (a === b || (b !== "/" && a.startsWith(b + "/"))) {
      if (b.length > maxLen) {
        maxLen = b.length;
        bestMatch = item;
      }
    }
  });

  if (bestMatch) {
    bestMatch.classList.add("sidebar__item--active");
    try {
      bestMatch.scrollIntoView({ block: "center", inline: "nearest" });
    } catch (e) {
      bestMatch.scrollIntoView(false);
    }
  }

  // Mobile menu toggle
  const mobileToggle = document.getElementById("mobile-menu-toggle");
  const sidebar = document.querySelector(".sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");

  if (mobileToggle && sidebar && backdrop) {
    const toggleMenu = () => {
      sidebar.classList.toggle("sidebar--open");
      backdrop.classList.toggle("sidebar-backdrop--visible");
    };
    const closeMenu = () => {
      sidebar.classList.remove("sidebar--open");
      backdrop.classList.remove("sidebar-backdrop--visible");
    };

    mobileToggle.addEventListener("click", toggleMenu);
    backdrop.addEventListener("click", closeMenu);
  }

  async function fetchNotificationCount() {
    try {
      const data = await panel.get("/api/notifications");
      const badge = document.getElementById("nav-notif-badge");
      if (badge && data.unread_count !== undefined) {
        if (data.unread_count > 0) {
          badge.textContent = data.unread_count;
          badge.style.display = "inline-block";
        } else {
          badge.style.display = "none";
        }
      }
    } catch (e) {
      console.error("Failed to fetch notification count", e);
    }
  }

  if (document.getElementById("nav-notif-badge")) {
    fetchNotificationCount();
    setInterval(fetchNotificationCount, 60000);
  }

  // Sidebar custom scroll logic
  const sidebarNav = document.getElementById("sidebar-nav");
  const scrollUpBtn = document.getElementById("sidebar-scroll-up");
  const scrollDownBtn = document.getElementById("sidebar-scroll-down");

  if (sidebarNav && scrollUpBtn && scrollDownBtn) {
    const updateScrollArrows = () => {
      const { scrollTop, scrollHeight, clientHeight } = sidebarNav;
      
      if (scrollTop > 0) {
        scrollUpBtn.classList.add("visible");
      } else {
        scrollUpBtn.classList.remove("visible");
      }
      
      if (Math.ceil(scrollTop + clientHeight) < scrollHeight) {
        scrollDownBtn.classList.add("visible");
      } else {
        scrollDownBtn.classList.remove("visible");
      }
    };

    sidebarNav.addEventListener("scroll", updateScrollArrows);
    window.addEventListener("resize", updateScrollArrows);
    // Initial check (delay slightly to ensure render)
    setTimeout(updateScrollArrows, 100);

    scrollUpBtn.addEventListener("click", () => {
      sidebarNav.scrollBy({ top: -200, behavior: "smooth" });
    });

    scrollDownBtn.addEventListener("click", () => {
      sidebarNav.scrollBy({ top: 200, behavior: "smooth" });
    });
  }
});

// ============================================================================
// GLOBAL PJAX ROUTER (ZERO RELOADS)
// ============================================================================

window.pjaxNavigate = async function(url, options = {}) {
  const method = options.method || "GET";
  // Always show loader for POST. For GET, we might want to just show it too.
  showGlobalLoader("Loading...");
  
  try {
    const res = await fetch(url, options);
    
    // If it's a JSON error, throw it
    const contentType = res.headers.get("content-type");
    if (contentType && contentType.includes("application/json") && !res.ok) {
      const json = await res.json().catch(() => ({}));
      throw new Error(json.detail || `Request failed (${res.status})`);
    }

    const html = await res.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");

    // Swap the main content area
    const currentMain = document.querySelector(".main");
    const newMain = doc.querySelector(".main");
    
    if (currentMain && newMain) {
      currentMain.innerHTML = newMain.innerHTML;
      
      // Update Title
      if (doc.title) {
        document.title = doc.title;
      }
      
      // Update URL
      if (options.pushState !== false) {
        window.history.pushState(null, "", res.url);
      }

      // Re-evaluate new scripts that might be in the new main block
      const scripts = currentMain.querySelectorAll("script");
      scripts.forEach((oldScript) => {
        const newScript = document.createElement("script");
        Array.from(oldScript.attributes).forEach(attr => newScript.setAttribute(attr.name, attr.value));
        newScript.textContent = oldScript.textContent;
        oldScript.parentNode.replaceChild(newScript, oldScript);
      });

      // Dispatch global init event so modules rebind
      document.dispatchEvent(new Event("app:init"));
      
      hideGlobalLoader();
      
      // If there's a toast in the response HTML, we might need to render it? 
      // Actually, standard toasts are rendered via flash messages in Python? No, toasts are JS only.
      // Wait, flash messages are in the HTML! So they will be swapped in and appear automatically!
    } else {
      // Fallback if structure is missing
      window.location.href = res.url;
    }
    
  } catch (err) {
    hideGlobalLoader();
    toast(err.message || "Navigation failed", "danger");
  }
};

document.addEventListener("click", (e) => {
  // Prevent intercepting modified clicks (ctrl, shift, meta)
  if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
  const link = e.target.closest("a");
  if (!link) return;
  
  const href = link.getAttribute("href");
  if (!href || href === "#" || href.startsWith("#") || href.startsWith("javascript:")) return;
  if (link.target && link.target !== "_self") return;
  if (link.hasAttribute("download") || link.hasAttribute("data-no-pjax")) return;

  try {
    const targetUrl = new URL(link.href, window.location.href);
    if (targetUrl.origin !== window.location.origin) return;
    
    e.preventDefault();
    pjaxNavigate(targetUrl.href);
  } catch(err) {
    return; // Fallback to standard link
  }
});

document.addEventListener("submit", (e) => {
  const form = e.target;
  if (form.hasAttribute("data-no-pjax")) return;
  
  // Exclude GET forms for now, or forms with target="_blank"
  if (form.method.toUpperCase() !== "POST" || form.target === "_blank") return;

  e.preventDefault();
  
  const formData = new FormData(form);
  // handle checkboxes
  form.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    if (!formData.has(cb.name)) {
      formData.set(cb.name, cb.checked ? "on" : ""); // Standard browser behavior handles checked checkboxes automatically, but just in case
    }
  });

  // Since it's a native form submit, we don't send application/json, 
  // but panel.post expects JSON. Wait! Normal forms in this app usually expect form-encoded or JSON?
  // Most of our custom actions use panel.post. 
  // Standard routes (like add domain) are probably expecting Form Data. Let's send it as FormData.
  pjaxNavigate(form.action, {
    method: "POST",
    body: formData
    // note: no csrfHeaders here because we are using FormData and the CSRF token is usually in the form as a hidden input.
  });
});

// Trigger initial app:init on first load
document.addEventListener("DOMContentLoaded", () => {
  document.dispatchEvent(new Event("app:init"));
});

window.PATHS = PATHS;
window.path = path;
window.publicUrl = publicUrl;
window.panel = panel;
window.submitPost = submitPost;
window.getCsrfToken = getCsrfToken;
window.csrfHeaders = csrfHeaders;
window.toast = toast;
window.openModal = openModal;
window.closeModal = closeModal;
window.hideSkeleton = hideSkeleton;
window.showSkeleton = showSkeleton;
window.confirmAction = confirmAction;
