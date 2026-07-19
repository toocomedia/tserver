/**
 * modules/errors.js — Admin error tracker UI.
 * Copy report to clipboard, clear-all confirm, delete confirm.
 */

document.addEventListener("DOMContentLoaded", () => {
  initCopyButtons();
  initClearAll();
  initDelete();
  dismissAlerts();
});

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // Fallback
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
}

function initCopyButtons() {
  document.querySelectorAll("[data-report-target]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-report-target");
      const el = document.getElementById(id);
      if (!el) {
        toast("Report not found", "danger");
        return;
      }
      const text = el.value !== undefined ? el.value : el.textContent;
      try {
        await copyText(text || "");
        toast("Report copied to clipboard", "success");
        const prev = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(() => {
          btn.textContent = prev;
        }, 1500);
      } catch (e) {
        toast("Copy failed: " + e.message, "danger");
      }
    });
  });
}

function initClearAll() {
  const btn = document.getElementById("btn-clear-all");
  if (!btn) return;
  btn.addEventListener("click", () => {
    confirmAction(
      "Delete ALL error records? This cannot be undone.",
      async () => {
        if (typeof window.submitPost === "function") {
          window.submitPost("/admin/errors/clear-all");
        } else {
          toast("Refresh the page (Ctrl+F5) and try again.", "danger");
        }
      }
    );
  });
}

function initDelete() {
  const btn = document.getElementById("btn-delete-error");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const id = btn.getAttribute("data-error-id");
    confirmAction(`Delete error #${id}?`, async () => {
      if (typeof window.submitPost === "function") {
        window.submitPost(`/admin/errors/${id}/delete`);
      } else {
        toast("Refresh the page (Ctrl+F5) and try again.", "danger");
      }
    });
  });
}

function dismissAlerts() {
  ["alert-error", "alert-ok"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) setTimeout(() => el.remove(), 6000);
  });
}
