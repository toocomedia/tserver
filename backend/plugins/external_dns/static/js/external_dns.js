/**
 * external_dns.js — External DNS connection management (plugin-owned).
 *
 * Builds the provider <select> and credential fields dynamically from
 * /plugins/external_dns/api/providers, so adding a provider needs no JS change.
 * Handles test / bind / unbind with in-place feedback and no polling reloads.
 * Loaded on the DNS records page (connect/manage) and the plugin landing page.
 */
(function () {
  "use strict";

  const API = "/plugins/external_dns/api";
  const t = (k) => (window._ ? window._(k) : k);
  let PROVIDERS = null;
  let currentDomain = null;

  function headers() {
    const h = { "Content-Type": "application/json", "Accept": "application/json" };
    if (window.getCsrfToken) { const tok = getCsrfToken(); if (tok) h["X-CSRF-Token"] = tok; }
    return h;
  }

  async function apiCall(path, body) {
    const res = await fetch(API + path, {
      method: body ? "POST" : "GET",
      headers: headers(),
      body: body ? JSON.stringify(body) : undefined,
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) console.warn("[external_dns]", path, res.status, json);
    return { ok: res.ok, data: json };
  }

  async function loadProviders() {
    if (PROVIDERS) return PROVIDERS;
    try { const r = await apiCall("/providers"); PROVIDERS = r.data.providers || []; }
    catch (e) { PROVIDERS = []; }
    return PROVIDERS;
  }

  const byId = (id) => (PROVIDERS || []).find((p) => p.id === id) || null;
  const zoneRef = () => (document.getElementById("ext-dns-zone-ref")?.value || "").trim();

  function renderCredFields(provider, masked) {
    const wrap = document.getElementById("ext-dns-cred-fields");
    if (!wrap) return;
    wrap.textContent = "";
    (provider?.credential_fields || []).forEach((f) => {
      const id = `ext-dns-cred-${f.id}`;
      const group = document.createElement("div");
      group.className = "form-group";

      const label = document.createElement("label");
      label.className = "form-label";
      label.setAttribute("for", id);
      label.textContent = t(f.label_key);

      const input = document.createElement("input");
      input.className = "form-input";
      input.id = id;
      input.name = f.id;
      input.type = f.type === "password" ? "password" : "text";
      input.autocomplete = "off";
      input.autocapitalize = "none";
      input.spellcheck = false;
      // setAttribute stores the value literally — a masked secret containing
      // quotes/angle brackets cannot break out into markup.
      const ph = (masked && masked[f.id]) || f.placeholder || "";
      if (ph) input.setAttribute("placeholder", ph);

      group.appendChild(label);
      group.appendChild(input);

      if (f.help_key) {
        const hint = document.createElement("span");
        hint.className = "form-hint text-muted";
        hint.textContent = t(f.help_key);
        group.appendChild(hint);
      }
      if (masked) {
        const keep = document.createElement("span");
        keep.className = "form-hint text-muted";
        keep.textContent = t("ext_dns_keep_blank");
        group.appendChild(keep);
      }
      wrap.appendChild(group);
    });
  }

  function renderSetupLink(provider) {
    const a = document.getElementById("ext-dns-setup-link");
    if (!a) return;
    if (provider && provider.setup_url) {
      a.href = provider.setup_url;
      a.textContent = `${provider.setup_label_key ? t(provider.setup_label_key) : t("ext_dns_setup_link")} ↗`;
      a.style.display = "inline-block";
    } else {
      a.style.display = "none";
      a.removeAttribute("href");
      a.textContent = "";
    }
  }

  function onProviderChange() {
    const p = byId(document.getElementById("ext-dns-provider")?.value);
    const help = document.getElementById("ext-dns-provider-help");
    if (help) help.textContent = p && p.help_key ? t(p.help_key) : "";
    renderSetupLink(p);
    renderCredFields(p, null);
  }

  function renderOptions(selectedId) {
    const sel = document.getElementById("ext-dns-provider");
    if (!sel) return;
    sel.textContent = "";
    (PROVIDERS || []).forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.id; opt.textContent = t(p.label_key);
      if (p.id === selectedId) opt.selected = true;
      sel.appendChild(opt);
    });
    const help = document.getElementById("ext-dns-provider-help");
    const cur = byId(sel.value);
    if (help) help.textContent = cur && cur.help_key ? t(cur.help_key) : "";
    renderSetupLink(cur);
    renderCredFields(cur, null);
  }

  function collect(provider) {
    const creds = {};
    (provider?.credential_fields || []).forEach((f) => {
      const el = document.getElementById(`ext-dns-cred-${f.id}`);
      if (el) creds[f.id] = el.value.trim();
    });
    return creds;
  }

  function showResult(ok, msg) {
    const box = document.getElementById("ext-dns-test-result");
    if (!box) return;
    box.className = `alert ${ok ? "alert--success" : "alert--danger"}`;
    box.textContent = msg;
    box.classList.remove("hidden");
  }

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) { btn.dataset.label = btn.dataset.label || btn.textContent; btn.disabled = true; btn.textContent = label; }
    else { btn.disabled = false; if (btn.dataset.label) btn.textContent = btn.dataset.label; }
  }

  async function doTest() {
    const p = byId(document.getElementById("ext-dns-provider")?.value);
    if (!p) return;
    const btn = document.getElementById("ext-dns-test-btn");
    setBusy(btn, true, t("ext_dns_testing"));
    try {
      const r = await apiCall("/test", { provider: p.id, credentials: collect(p), zone_ref: zoneRef() });
      showResult(!!r.data.ok, r.data.ok ? t("ext_dns_test_ok") : (r.data.error || t("ext_dns_test_fail")));
    } catch (e) { showResult(false, e.message || t("ext_dns_test_fail")); }
    finally { setBusy(btn, false); }
  }

  async function doSave() {
    const p = byId(document.getElementById("ext-dns-provider")?.value);
    if (!p || !currentDomain) return;
    const btn = document.getElementById("ext-dns-save-btn");
    setBusy(btn, true, t("ext_dns_saving"));
    try {
      const r = await apiCall("/bind", { domain: currentDomain, provider: p.id, credentials: collect(p), zone_ref: zoneRef() });
      if (r.data.ok) {
        if (window.toast) toast(t("ext_dns_connected"), "success");
        if (window.refreshTasks) refreshTasks();
        if (window.closeModal) closeModal("ext-dns-modal");
        window.location.href = `/dns/${encodeURIComponent(currentDomain)}/records`;
      } else {
        showResult(false, r.data.error || t("ext_dns_test_fail"));
        setBusy(btn, false);
      }
    } catch (e) { showResult(false, e.message || t("ext_dns_test_fail")); setBusy(btn, false); }
  }

  window.openExternalDnsModal = async function (domain, binding) {
    currentDomain = domain;
    await loadProviders();
    if (binding === undefined) {
      // Fetch the current (masked) binding, if any, to decide connect vs manage.
      try { const r = await apiCall(`/binding/${encodeURIComponent(domain)}`); binding = r.data.binding || null; }
      catch (e) { binding = null; }
    }
    const domEl = document.getElementById("ext-dns-modal-domain");
    if (domEl) domEl.textContent = domain || "";
    const titleEl = document.getElementById("ext-dns-modal-title");
    if (titleEl) titleEl.textContent = binding ? t("ext_dns_edit_title") : t("ext_dns_connect_title");
    const zoneEl = document.getElementById("ext-dns-zone-ref");
    if (zoneEl) { zoneEl.value = binding?.zone_ref || domain || ""; zoneEl.placeholder = domain || ""; }
    const res = document.getElementById("ext-dns-test-result");
    if (res) { res.classList.add("hidden"); res.textContent = ""; }
    renderOptions(binding?.provider || (PROVIDERS[0] && PROVIDERS[0].id));
    if (binding?.credentials_masked) renderCredFields(byId(binding.provider), binding.credentials_masked);
    if (window.openModal) openModal("ext-dns-modal");
  };

  function bindUnbindButtons() {
    document.querySelectorAll(".ext-dns-unbind").forEach((btn) => {
      btn.addEventListener("click", () => {
        const domain = btn.getAttribute("data-domain");
        if (!window.confirmAction) return;
        confirmAction(t("ext_dns_disconnect_confirm"), async () => {
          try {
            const r = await apiCall("/unbind", { domain });
            if (r.data.ok !== false) {
              if (window.toast) toast(t("ext_dns_disconnected"), "success");
              if (window.refreshTasks) refreshTasks();
              // Reverting to PowerDNS changes this row's badge/actions (and the
              // landing list), so refresh once — a user-initiated, one-time reload.
              setTimeout(() => window.location.reload(), 500);
            } else if (window.toast) toast(r.data.error || "Error", "danger");
          } catch (e) { if (window.toast) toast(e.message || "Error", "danger"); }
        }, { danger: true, title: t("ext_dns_disconnect_title"), okLabel: t("ext_dns_disconnect"), itemName: domain });
      });
    });
  }

  function bindSyncButtons() {
    document.querySelectorAll(".ext-dns-sync").forEach((btn) => {
      btn.addEventListener("click", () => {
        const domain = btn.getAttribute("data-domain");
        if (!domain || !window.confirmAction) return;
        confirmAction(t("ext_dns_sync_confirm"), async () => {
          btn.disabled = true;
          try {
            const res = await fetch(`/dns/${encodeURIComponent(domain)}/records/sync`, { method: "POST", headers: headers() });
            const data = await res.json().catch(() => ({}));
            if (res.ok && data.status === "ok") {
              if (window.toast) toast(data.message || t("ext_dns_sync"), "success");
              if (window.refreshTasks) refreshTasks();
            } else if (window.toast) {
              toast(data.error || data.detail || "Error", "danger");
            }
          } catch (e) { if (window.toast) toast(e.message || "Error", "danger"); }
          finally { btn.disabled = false; }
        }, { title: t("ext_dns_sync_title"), okLabel: t("ext_dns_sync"), itemName: domain });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.getElementById("ext-dns-provider")?.addEventListener("change", onProviderChange);
    document.getElementById("ext-dns-test-btn")?.addEventListener("click", doTest);
    document.getElementById("ext-dns-save-btn")?.addEventListener("click", doSave);
    bindUnbindButtons();
    bindSyncButtons();
  });
})();
