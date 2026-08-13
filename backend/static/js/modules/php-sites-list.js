const root = document.querySelector("[data-php-sites-list]");

if (root) {
  const dismiss = () => {
    if (typeof window.hideSkeleton === "function") {
      window.hideSkeleton("php-sites-list-skeleton", 0);
    }
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", dismiss, { once: true });
  } else {
    dismiss();
  }
}
