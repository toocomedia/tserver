(() => {
  const storageKey = "srv-panel-resource-guard-notice-dismissed";

  function updateRecommendation(status) {
    const card = document.getElementById("resource-guard-recommendation");
    if (!card) return;
    card.hidden = localStorage.getItem(storageKey) === "true" || !status.is_low_ram || status.enabled;
  }

  async function refreshGuardBanner() {
    const banner = document.getElementById("resource-guard-banner");
    if (!banner) return;
    try {
      const status = await panel.get("/api/resource-guard/status");
      const active = status.state !== "normal";
      banner.hidden = !active;
      if (active) banner.textContent = `Resource Guard: ${status.ram_percent}% RAM used (safe limit ${status.limit_percent}%). ${status.state === "active" ? "Heavy panel actions are paused." : "Unmanaged host usage detected."}`;
      updateRecommendation(status);
    } catch (_) {
      banner.hidden = true;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.addEventListener("click", (event) => {
      const action = event.target.closest("[data-resource-guard-dismiss]")?.dataset.resourceGuardDismiss;
      if (!action) return;
      if (action === "forever") localStorage.setItem(storageKey, "true");
      const card = document.getElementById("resource-guard-recommendation");
      if (card) card.hidden = true;
    });
    refreshGuardBanner();
    window.setInterval(refreshGuardBanner, 15000);
  });
})();
