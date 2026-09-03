/**
 * external_dns_records.js — Records-page wiring for external DNS providers.
 *
 * Reuses the existing "Add DNS Record" modal as an editor for external rows and
 * submits edits via AJAX, updating the row in place (no full reload). Loaded only
 * when the external_dns plugin is active and the domain is bound.
 *
 * Depends on globals from the records page: CURRENT_DOMAIN, and helpers from
 * main.js (openModal/closeModal/toast/getCsrfToken) + dns.js (updateContentLabel).
 */
(function () {
  "use strict";

  const t = (k) => (window._ ? window._(k) : k);
  const domain = () => (typeof CURRENT_DOMAIN !== "undefined" ? CURRENT_DOMAIN : "");
  let editingRow = null;

  const form = () => document.getElementById("add-record-form");
  const field = (id) => document.getElementById(id);

  function setEditMode(btn) {
    const f = form();
    if (!f) return;
    editingRow = btn.closest("tr");
    f.dataset.mode = "edit";
    f.setAttribute("action", `/dns/${encodeURIComponent(domain())}/records/edit`);

    let rid = f.querySelector('input[name="record_id"]');
    if (!rid) {
      rid = document.createElement("input");
      rid.type = "hidden"; rid.name = "record_id";
      f.appendChild(rid);
    }
    rid.value = btn.dataset.id || "";

    if (field("rec-type")) field("rec-type").value = btn.dataset.type || "A";
    if (field("rec-name")) field("rec-name").value = btn.dataset.name || "";
    if (field("rec-content")) field("rec-content").value = btn.dataset.content || "";
    if (field("rec-ttl")) field("rec-ttl").value = btn.dataset.ttl || "3600";
    if (typeof updateContentLabel === "function") updateContentLabel(btn.dataset.type || "A");

    const title = field("add-modal-title");
    if (title) { title.dataset.orig = title.dataset.orig || title.textContent; title.textContent = t("ext_dns_edit_record_title"); }
    const save = field("btn-save-record");
    if (save) { save.dataset.orig = save.dataset.orig || save.textContent; save.textContent = t("ext_dns_save_record"); save.disabled = false; }

    if (window.openModal) openModal("add-record-modal");
  }

  function resetAddMode() {
    const f = form();
    if (!f || f.dataset.mode !== "edit") return;
    f.dataset.mode = "";
    f.setAttribute("action", `/dns/${encodeURIComponent(domain())}/records/add`);
    const rid = f.querySelector('input[name="record_id"]');
    if (rid) rid.remove();
    if (typeof f.reset === "function") f.reset();
    const title = field("add-modal-title");
    if (title && title.dataset.orig) title.textContent = title.dataset.orig;
    const save = field("btn-save-record");
    if (save && save.dataset.orig) { save.textContent = save.dataset.orig; save.disabled = false; }
    editingRow = null;
  }

  function updateRowInPlace(rec) {
    if (!editingRow || !rec) return;
    const set = (sel, val) => { const el = editingRow.querySelector(sel); if (el) el.textContent = val; };
    set('[data-cell="name"]', rec.name);
    set('[data-cell="type"]', rec.type);
    set('[data-cell="content"]', rec.content);
    set('[data-cell="ttl"]', rec.ttl);
    editingRow.setAttribute("data-record-id", rec.id);
    const edit = editingRow.querySelector(".btn-edit-record");
    if (edit) Object.assign(edit.dataset, { id: rec.id, name: rec.name, type: rec.type, content: rec.content, ttl: rec.ttl });
    const del = editingRow.querySelector(".btn-del-record");
    if (del) Object.assign(del.dataset, { id: rec.id, name: rec.name, type: rec.type, content: rec.content });
  }

  async function submitEdit(e) {
    const f = form();
    if (!f || f.dataset.mode !== "edit") return;   // normal add proceeds untouched
    e.preventDefault();
    const save = field("btn-save-record");
    if (save) { save.disabled = true; }
    const body = new URLSearchParams({
      record_id: (f.querySelector('input[name="record_id"]') || {}).value || "",
      name: field("rec-name")?.value || "",
      type: field("rec-type")?.value || "",
      content: field("rec-content")?.value || "",
      ttl: field("rec-ttl")?.value || "3600",
      csrf_token: window.getCsrfToken ? getCsrfToken() : "",
    });
    try {
      const res = await fetch(f.getAttribute("action"), {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json",
          "X-CSRF-Token": window.getCsrfToken ? getCsrfToken() : "",
        },
        body,
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        updateRowInPlace(data.record);
        if (window.toast) toast(data.message || t("ext_dns_record_updated"), "success");
        if (window.closeModal) closeModal("add-record-modal");
        resetAddMode();
        if (window.refreshTasks) refreshTasks();
      } else {
        if (window.toast) toast(data.error || data.detail || "Error", "danger");
        if (save) save.disabled = false;
      }
    } catch (err) {
      if (window.toast) toast(err.message || "Error", "danger");
      if (save) save.disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".btn-edit-record").forEach((btn) => {
      btn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); setEditMode(btn); });
    });
    form()?.addEventListener("submit", submitEdit);
    // Opening the plain "Add Record" modal must clear any stale edit state.
    field("btn-add-record")?.addEventListener("click", () => setTimeout(resetAddMode, 0));
  });
})();
