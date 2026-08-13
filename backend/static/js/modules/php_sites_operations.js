/**
 * Operation Progress Polling Helper
 * Polls GET /api/php-sites/operations/{id} until terminal state succeeded or failed.
 */

function extractErrorMessage(err) {
  if (!err) return "An unexpected error occurred.";
  if (typeof err === "string") return err;
  
  if (err.detail !== undefined && err.detail !== null) {
    return extractErrorMessage(err.detail);
  }
  if (err.error !== undefined && err.error !== null) {
    return extractErrorMessage(err.error);
  }
  if (typeof err.message === "string") return err.message;
  if (typeof err.reason === "string") return err.reason;

  if (Array.isArray(err)) {
    return err.map(item => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const field = Array.isArray(item.loc) ? item.loc.filter(x => x !== "body").join(".") : "";
        const msg = item.msg || item.message || item.detail || item.reason || JSON.stringify(item);
        return field ? `${field}: ${msg}` : msg;
      }
      return String(item);
    }).join("\n");
  }

  try {
    return JSON.stringify(err, null, 2);
  } catch (_) {
    return String(err);
  }
}

export function showOperationModal(title) {
  const modal = document.getElementById("operation-modal");
  const titleEl = document.getElementById("op-modal-title");
  const badgeEl = document.getElementById("op-stage-badge");
  const msgEl = document.getElementById("op-message-text");
  const errEl = document.getElementById("op-error-box");
  const footerEl = document.getElementById("op-modal-footer");
  const spinner = document.getElementById("op-spinner");

  if (titleEl) titleEl.textContent = title || "Executing Operation...";
  if (badgeEl) badgeEl.textContent = "QUEUED";
  if (msgEl) msgEl.textContent = "Starting operation...";
  if (errEl) errEl.style.display = "none";
  if (footerEl) footerEl.style.display = "none";
  if (spinner) spinner.style.display = "block";
  if (modal) modal.style.display = "flex";
}

export function hideOperationModal() {
  const modal = document.getElementById("operation-modal");
  if (modal) modal.style.display = "none";
}

export async function pollOperation(operationId, onSuccess, onError) {
  const badgeEl = document.getElementById("op-stage-badge");
  const msgEl = document.getElementById("op-message-text");
  const errEl = document.getElementById("op-error-box");
  const footerEl = document.getElementById("op-modal-footer");
  const spinner = document.getElementById("op-spinner");

  const interval = setInterval(async () => {
    try {
      const res = await fetch(`/api/php-sites/operations/${operationId}`);
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(extractErrorMessage(errData));
      }
      const data = await res.json();
      
      if (badgeEl) badgeEl.textContent = (data.stage || data.status || "RUNNING").toUpperCase();
      if (msgEl) msgEl.textContent = data.message || "Processing...";

      if (data.status === "succeeded") {
        clearInterval(interval);
        if (spinner) spinner.style.display = "none";
        if (msgEl) msgEl.textContent = "Operation completed successfully!";
        setTimeout(() => {
          hideOperationModal();
          if (typeof onSuccess === "function") onSuccess(data);
        }, 800);
      } else if (data.status === "failed") {
        clearInterval(interval);
        if (spinner) spinner.style.display = "none";
        const errorMsg = extractErrorMessage(data.error || "Operation failed.");
        if (errEl) {
          errEl.textContent = errorMsg;
          errEl.style.display = "block";
        }
        if (footerEl) footerEl.style.display = "flex";
        if (typeof onError === "function") onError(errorMsg);
      }
    } catch (err) {
      clearInterval(interval);
      if (spinner) spinner.style.display = "none";
      const errorMsg = extractErrorMessage(err);
      if (errEl) {
        errEl.textContent = errorMsg;
        errEl.style.display = "block";
      }
      if (footerEl) footerEl.style.display = "flex";
      if (typeof onError === "function") onError(errorMsg);
    }
  }, 1200);
}

document.addEventListener("DOMContentLoaded", () => {
  const closeBtn = document.getElementById("btn-close-op-modal");
  if (closeBtn) {
    closeBtn.addEventListener("click", hideOperationModal);
  }
});
