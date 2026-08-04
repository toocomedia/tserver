(() => {
  async function refreshGuardBanner() {
    const banner = document.getElementById("resource-guard-banner");
    if (!banner) return;
    try {
      const status = await panel.get("/api/resource-guard/status");
      const active = status.state !== "normal";
      banner.hidden = !active;
      if (active) banner.textContent = `Resource Guard: ${status.ram_percent}% RAM used (safe limit ${status.limit_percent}%). ${status.state === "active" ? "Heavy panel actions are paused." : "Unmanaged host usage detected."}`;
    } catch (_) {
      banner.hidden = true;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    refreshGuardBanner();
    window.setInterval(refreshGuardBanner, 15000);
  });
})();
