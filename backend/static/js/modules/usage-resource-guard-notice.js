(() => {
  const storageKey = "srv-panel-resource-guard-notice-dismissed";
  const notice = () => document.getElementById("usage-resource-guard-alert");

  function update(detail) {
    const card = notice();
    if (!card || localStorage.getItem(storageKey) === "true") return;
    if (!detail.ram?.is_low_ram || !detail.guard) {
      card.hidden = true;
      return;
    }
    const copy = document.getElementById("usage-resource-guard-copy");
    copy.textContent = detail.guard.enabled
      ? "Resource Guard is enabled for this low-RAM VPS. Review its limit and priorities in Settings."
      : "Resource Guard Recommended: enable it to stop a heavy panel action from exhausting this low-RAM VPS.";
    card.hidden = false;
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.addEventListener("click", (event) => {
      const action = event.target.closest("[data-resource-guard-dismiss]")?.dataset.resourceGuardDismiss;
      if (!action) return;
      if (action === "forever") localStorage.setItem(storageKey, "true");
      const card = notice();
      if (card) card.hidden = true;
    });
    window.addEventListener("usage:resource-state", (event) => update(event.detail));
  });
})();
