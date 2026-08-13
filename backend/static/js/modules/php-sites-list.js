const root = document.querySelector("[data-php-sites-list]");

// Match the server-rendered Domains page: rows arrive with the HTML, then the
// shared overlay fades after DOMContentLoaded using the standard delay.
if (root) {
  document.addEventListener("DOMContentLoaded", () => {
    if (typeof window.hideSkeleton === "function") window.hideSkeleton("php-sites-list-skeleton");
  }, { once: true });
}
