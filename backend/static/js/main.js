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
  php_sites: "/php-sites/",
  php_sites_create: "/php-sites/create",
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

/** Standard check whether a resource, domain, or site has active SSL. */
function isSslActive(resource) {
  if (!resource) return false;
  if (typeof resource === "boolean") return resource;
  if (typeof resource.ssl_active === "boolean") return resource.ssl_active;
  if (resource.ssl && typeof resource.ssl.active === "boolean") return resource.ssl.active;
  if (typeof resource.ssl_requested === "boolean") return resource.ssl_requested;
  return false;
}
window.publicUrl = publicUrl;
window.isSslActive = isSslActive;

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
  const button = e.submitter || e.target.querySelector("button[type=submit]");
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
  const danger = options ? !!opts.danger : true;

  if (danger && typeof window.openDeleteDrawer === "function") {
    window.openDeleteDrawer({
      title: opts.title,
      message: message,
      itemName: opts.itemName,
      okLabel: opts.okLabel,
      onConfirm: onConfirm
    });
    return;
  }

  const title = opts.title || "Confirm Action";
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

document.addEventListener("DOMContentLoaded", () => {
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

  // Instant active state on click (zero-latency visual feedback before navigation)
  document.querySelectorAll(".sidebar__item").forEach((item) => {
    item.addEventListener("click", (e) => {
      if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
        const href = item.getAttribute("href");
        if (href && href !== "#" && !href.startsWith("javascript:")) {
          document.querySelectorAll(".sidebar__item--active").forEach((el) => {
            el.classList.remove("sidebar__item--active");
          });
          item.classList.add("sidebar__item--active");
        }
      }
    });
  });

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

  // Sidebar custom scroll logic + Plugins Services view swapping
  const sidebarNav = document.getElementById("sidebar-nav");
  const scrollUpBtn = document.getElementById("sidebar-scroll-up");
  const scrollDownBtn = document.getElementById("sidebar-scroll-down");
  const pluginsView = document.getElementById("sidebar-plugins-view");
  const pluginsNav = pluginsView ? pluginsView.querySelector(".sidebar__nav") : null;
  let updateScrollArrows = null;

  if (sidebarNav && scrollUpBtn && scrollDownBtn) {
    const navLists = [sidebarNav, pluginsNav].filter(Boolean);
    const getActiveNav = () => {
      return navLists.find((nav) => {
        const view = nav.classList.contains("sidebar__view")
          ? nav
          : nav.closest(".sidebar__view");
        return view ? view.classList.contains("is-active") : true;
      }) || sidebarNav;
    };

    updateScrollArrows = () => {
      const { scrollTop, scrollHeight, clientHeight } = getActiveNav();

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

    navLists.forEach((nav) => nav.addEventListener("scroll", updateScrollArrows));
    window.addEventListener("resize", updateScrollArrows);
    // Initial check (delay slightly to ensure render)
    setTimeout(updateScrollArrows, 100);

    scrollUpBtn.addEventListener("click", () => {
      getActiveNav().scrollBy({ top: -200, behavior: "smooth" });
    });

    scrollDownBtn.addEventListener("click", () => {
      getActiveNav().scrollBy({ top: 200, behavior: "smooth" });
    });
  }

  // Plugins Services sidebar tab: toggle main <-> plugins views
  if (pluginsView && sidebarNav) {
    const pluginsTab = document.getElementById("plugins-services-tab");
    const pluginsBack = document.getElementById("plugins-services-back");

    const setView = (toPlugins) => {
      pluginsView.classList.toggle("is-active", toPlugins);
      sidebarNav.classList.toggle("is-active", !toPlugins);
      if (pluginsTab) {
        pluginsTab.setAttribute("aria-expanded", toPlugins);
        const onPluginPage = !!(bestMatch && pluginsView.contains(bestMatch));
        pluginsTab.classList.toggle("sidebar__section-tab--active", !toPlugins && onPluginPage);
      }
      if (updateScrollArrows) updateScrollArrows();
    };

    pluginsTab?.addEventListener("click", () => setView(true));
    pluginsBack?.addEventListener("click", () => setView(false));

    // Only open plugins view if current page is actually a service item in the submenu
    const isChildPlugin = !!(bestMatch && pluginsView.contains(bestMatch));
    setView(isChildPlugin);
  }

  // Sidebar Search
  const sidebarSearchBtn = document.getElementById("sidebar-search-btn");
  const sidebarSearchInput = document.getElementById("sidebar-search-input");
  const sidebarSearchContainer = document.getElementById("sidebar-search");
  
  if (sidebarSearchBtn && sidebarSearchInput && sidebarSearchContainer && sidebarNav) {
    sidebarSearchBtn.addEventListener("click", () => {
      sidebarSearchContainer.classList.toggle("is-expanded");
      if (sidebarSearchContainer.classList.contains("is-expanded")) {
        sidebarSearchInput.focus();
      } else {
        sidebarSearchInput.value = "";
        sidebarSearchInput.dispatchEvent(new Event('input')); // trigger reset
      }
    });

    // Close when clicking outside
    document.addEventListener("click", (e) => {
      if (!sidebarSearchContainer.contains(e.target) && sidebarSearchContainer.classList.contains("is-expanded") && !sidebarSearchInput.value) {
        sidebarSearchContainer.classList.remove("is-expanded");
      }
    });

    sidebarSearchInput.addEventListener("input", (e) => {
      const term = e.target.value.toLowerCase().trim();

      const filterList = (listEl) => {
        let currentSectionLabel = null;
        let sectionHasVisibleItems = false;

        listEl.querySelectorAll("li").forEach(li => {
          if (li.classList.contains("sidebar__section-label") || li.querySelector(".sidebar__section-tab")) {
            // If we had a previous section, hide it if it had no visible items
            if (currentSectionLabel && !sectionHasVisibleItems) {
              currentSectionLabel.style.display = "none";
            }
            currentSectionLabel = li.classList.contains("sidebar__section-label") ? li : null;
            sectionHasVisibleItems = false; // reset for new section
            // Show by default unless we hide it later
            li.style.display = "";
          } else {
            // Regular item
            const text = li.textContent.toLowerCase();
            if (text.includes(term)) {
              li.style.display = "";
              sectionHasVisibleItems = true;
            } else {
              li.style.display = "none";
            }
          }
        });

        // Check last section
        if (currentSectionLabel && !sectionHasVisibleItems) {
          currentSectionLabel.style.display = "none";
        }
      };

      [sidebarNav, pluginsNav].filter(Boolean).forEach(filterList);

      // Update arrows
      if (updateScrollArrows) updateScrollArrows();
    });
  }

  // Initialize Lazy Image Skeleton Loaders
  initLazyImageSkeletons();
  document.addEventListener("app:init", initLazyImageSkeletons);

  // Trigger app:init so page-specific modules can initialize
  document.dispatchEvent(new Event("app:init"));
});

document.addEventListener("asyncLoaded", function() {
  if (typeof initLazyImageSkeletons === "function") {
    initLazyImageSkeletons();
  }
});

/**
 * Lazy Image Skeleton Loader: automatically marks image boxes as loaded or error,
 * and handles pre-cached images gracefully.
 */
function initLazyImageSkeletons() {
  const images = document.querySelectorAll(
    ".img-skeleton-box img, .plugin-card__cover img, .compact-card-1x1__icon img, .info-hero-row__icon img, .list-col-thumb img, .dependency-card__cover img"
  );
  images.forEach((img) => {
    const parent = img.parentElement;
    if (!parent) return;

    if (
      !parent.classList.contains("img-skeleton-box") &&
      !parent.classList.contains("is-loaded") &&
      !parent.classList.contains("is-error")
    ) {
      parent.classList.add("img-skeleton-box");
    }

    if (img.complete && img.naturalWidth !== 0) {
      parent.classList.add("is-loaded");
      img.classList.add("is-loaded");
    } else {
      img.addEventListener("load", () => {
        parent.classList.add("is-loaded");
        img.classList.add("is-loaded");
      });
      img.addEventListener("error", () => {
        parent.classList.remove("img-skeleton-box");
        parent.classList.add("is-error");
      });
    }
  });
}

function convertSubdomainToRecord(subdomain, parentDomain) {
  confirmAction(
    `Convert '${subdomain}' from a standalone DNS zone into an A record inside '${parentDomain}'? The separate zone will be deleted.`,
    async () => {
      if (typeof showGlobalLoader === "function") {
        showGlobalLoader("Converting DNS Zone...");
      }
      try {
        const res = await panel.post(`/dns/api/${encodeURIComponent(subdomain)}/convert-to-record`);
        if (typeof hideGlobalLoader === "function") hideGlobalLoader();
        if (typeof toast === "function") {
          toast(`Converted '${subdomain}' to an A record in '${parentDomain}'`, "success");
        }
        if (res && res.redirect_url) {
          window.location.href = res.redirect_url;
        } else {
          window.location.reload();
        }
      } catch (err) {
        if (typeof hideGlobalLoader === "function") hideGlobalLoader();
        if (typeof toast === "function") {
          toast(err.message || "Failed to convert DNS zone.", "danger");
        } else {
          alert(err.message || "Failed to convert DNS zone.");
        }
      }
    },
    {
      title: "Convert Subdomain to Record",
      okLabel: "Convert to Record",
      danger: false,
    }
  );
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}


window.escapeHtml = escapeHtml;
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
window.initLazyImageSkeletons = initLazyImageSkeletons;
window.convertSubdomainToRecord = convertSubdomainToRecord;
