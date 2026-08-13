/**
 * Operation Progress Polling Helper
 * Polls GET /api/php-sites/operations/{id} until terminal state succeeded or failed.
 */

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
        throw new Error(`Failed to poll operation (HTTP ${res.status})`);
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
        if (errEl) {
          errEl.textContent = data.error || "Operation failed.";
          errEl.style.display = "block";
        }
        if (footerEl) footerEl.style.display = "flex";
        if (typeof onError === "function") onError(data.error);
      }
    } catch (err) {
      clearInterval(interval);
      if (spinner) spinner.style.display = "none";
      if (errEl) {
        errEl.textContent = err.message;
        errEl.style.display = "block";
      }
      if (footerEl) footerEl.style.display = "flex";
      if (typeof onError === "function") onError(err.message);
    }
  }, 1200);
}

document.addEventListener("DOMContentLoaded", () => {
  const closeBtn = document.getElementById("btn-close-op-modal");
  if (closeBtn) {
    closeBtn.addEventListener("click", hideOperationModal);
  }
});
