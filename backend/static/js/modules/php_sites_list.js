/**
 * JS Module for PHP Sites list page (index.html)
 */

window.filterPhpSitesTable = function () {
  const searchInput = document.getElementById("php-site-search");
  const presetFilter = document.getElementById("php-preset-filter");
  const table = document.getElementById("php-sites-table");
  if (!table) return;

  const query = (searchInput ? searchInput.value : "").toLowerCase();
  const preset = presetFilter ? presetFilter.value : "all";
  const rows = table.querySelectorAll("tbody tr");

  rows.forEach((row) => {
    const text = row.textContent.toLowerCase();
    const rowPreset = row.dataset.preset || "";
    const matchesSearch = !query || text.includes(query);
    const matchesPreset = preset === "all" || rowPreset === preset;

    if (matchesSearch && matchesPreset) {
      row.style.display = "";
    } else {
      row.style.display = "none";
    }
  });
};
