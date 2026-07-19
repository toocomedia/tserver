/**
 * settings.js — Panel hostname, IP, SSL progress, security
 */
(function () {
  function $(id) {
    return document.getElementById(id);
  }

  function selectedUrlMode() {
    const el = document.querySelector('input[name="url_mode"]:checked');
    return el ? el.value : "none";
  }

  function computedHostname() {
    const mode = selectedUrlMode();
    if (mode === "custom") {
      return ($("custom_domain")?.value || "").trim().toLowerCase();
    }
    if (mode === "subdomain") {
      const label = ($("subdomain_label")?.value || "panel").trim().toLowerCase() || "panel";
      const parent = ($("parent_domain")?.value || "").trim().toLowerCase();
      return parent ? `${label}.${parent}` : "";
    }
    return "";
  }

  function syncUrlModeUi() {
    const mode = selectedUrlMode();
    if ($("url-custom-fields")) $("url-custom-fields").hidden = mode !== "custom";
    if ($("url-subdomain-fields")) $("url-subdomain-fields").hidden = mode !== "subdomain";

    const host = computedHostname();
    if ($("result-hostname")) $("result-hostname").textContent = host || "(IP only)";
    if ($("btn-issue-ssl")) $("btn-issue-ssl").disabled = !host;

    if (mode === "custom" && $("custom-dns-hint")) {
      const ip = $("stat-server-ip")?.textContent || "SERVER_IP";
      $("custom-dns-hint").textContent = `${host || "hostname"} → ${ip}`;
    }
    if (mode === "subdomain" && $("subdomain-preview")) {
      const label = ($("subdomain_label")?.value || "panel").trim() || "panel";
      const parent = ($("parent_domain")?.value || "").trim() || "example.com";
      $("subdomain-preview").textContent = `${label}.${parent}`;
    }
    if ($("ip-port-group") && $("allow_ip")) {
      $("ip-port-group").style.opacity = $("allow_ip").checked ? "1" : "0.5";
    }
    document.querySelectorAll(".settings-choice").forEach((card) => {
      const radio = card.querySelector('input[type="radio"]');
      card.classList.toggle("settings-choice--active", !!(radio && radio.checked));
    });
  }

  function readPayload() {
    return {
      url_mode: selectedUrlMode(),
      custom_domain: ($("custom_domain")?.value || "").trim(),
      parent_domain: ($("parent_domain")?.value || "").trim(),
      subdomain_label: ($("subdomain_label")?.value || "panel").trim(),
      panel_domain: computedHostname(),
      allow_ip: !!$("allow_ip")?.checked,
      ip_port: parseInt($("ip_port")?.value || "80", 10),
      session_https_only: !!$("session_https_only")?.checked,
      security_headers: !!$("security_headers")?.checked,
      hsts_enabled: !!$("hsts_enabled")?.checked,
      session_max_age_days: parseInt($("session_max_age_days")?.value || "7", 10),
    };
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showNotes(notes, type) {
    const box = $("settings-notes");
    if (!box) return;
    if (!notes || !notes.length) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.className = `alert alert--${type || "success"} mb-lg`;
    box.innerHTML =
      "<ul style='margin:0;padding-left:1.2em'>" +
      notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("") +
      "</ul>";
  }

  function fillParentSelect(managed, selected) {
    const sel = $("parent_domain");
    if (!sel) return;
    const current = selected || sel.value;
    sel.innerHTML = '<option value="">Select domain…</option>';
    (managed || []).forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d;
      if (d === current) opt.selected = true;
      sel.appendChild(opt);
    });
    if ($("url_mode_subdomain")) {
      $("url_mode_subdomain").disabled = !(managed && managed.length);
    }
  }

  function applyStatus(s) {
    if (!s) return;
    fillParentSelect(s.managed_domains, s.parent_domain);

    const mode = s.url_mode || (s.panel_domain ? "custom" : "none");
    const radio = document.querySelector(`input[name="url_mode"][value="${mode}"]`);
    if (radio && !radio.disabled) radio.checked = true;
    else if ($("url_mode_none")) $("url_mode_none").checked = true;

    if ($("custom_domain") && document.activeElement !== $("custom_domain")) {
      $("custom_domain").value = mode === "custom" ? s.panel_domain || "" : "";
    }
    if ($("subdomain_label") && document.activeElement !== $("subdomain_label")) {
      $("subdomain_label").value = s.subdomain_label || "panel";
    }
    if ($("parent_domain") && s.parent_domain) $("parent_domain").value = s.parent_domain;
    if ($("allow_ip")) $("allow_ip").checked = !!s.allow_ip;
    if ($("ip_port") && document.activeElement !== $("ip_port")) {
      $("ip_port").value = s.ip_port || 80;
    }
    if ($("session_https_only")) $("session_https_only").checked = !!s.session_https_only;
    if ($("security_headers")) $("security_headers").checked = !!s.security_headers;
    if ($("hsts_enabled")) $("hsts_enabled").checked = !!s.hsts_enabled;
    if ($("session_max_age_days")) $("session_max_age_days").value = s.session_max_age_days || 7;
    if ($("stat-server-ip")) $("stat-server-ip").textContent = s.server_ip || "";
    if ($("stat-hostname")) {
      $("stat-hostname").textContent = s.panel_domain || "— (IP only)";
    }
    if ($("stat-ssl")) {
      $("stat-ssl").innerHTML = s.ssl_active
        ? '<span class="badge badge--ok">HTTPS</span>'
        : '<span class="badge badge--neutral">off</span>';
    }
    if ($("stat-dns")) {
      if (!s.panel_domain) $("stat-dns").innerHTML = '<span class="text-muted">—</span>';
      else if (s.dns_ok === true) $("stat-dns").innerHTML = '<span class="badge badge--ok">OK</span>';
      else if (s.dns_ok === false) $("stat-dns").innerHTML = '<span class="badge badge--error">wrong IP</span>';
      else $("stat-dns").innerHTML = '<span class="badge badge--neutral">?</span>';
    }
    if ($("stat-urls") && s.urls) {
      // Prefer server-provided URLs (already normalized with trailing /)
      const parts = [];
      const ip = s.urls.ip_http || (s.allow_ip && s.server_ip
        ? (typeof publicUrl === "function"
            ? publicUrl(s.server_ip, { port: s.ip_port || 80 })
            : null)
        : null);
      const http = s.urls.domain_http || (s.panel_domain && typeof publicUrl === "function"
        ? publicUrl(s.panel_domain)
        : s.urls.domain_http);
      const https = s.urls.domain_https || (s.panel_domain && s.ssl_active && typeof publicUrl === "function"
        ? publicUrl(s.panel_domain, { https: true })
        : s.urls.domain_https);
      if (ip) parts.push(`<div>IP: <a href="${ip}" target="_blank" rel="noopener">${ip}</a></div>`);
      if (http) parts.push(`<div>HTTP: <a href="${http}" target="_blank" rel="noopener">${http}</a></div>`);
      if (https) parts.push(`<div>HTTPS: <a href="${https}" target="_blank" rel="noopener">${https}</a></div>`);
      $("stat-urls").innerHTML = parts.join("") || "—";
    }
    syncUrlModeUi();
  }

  /* ── SSL progress UI ── */
  function showSslProgress(show) {
    const box = $("ssl-progress");
    if (box) box.hidden = !show;
  }

  function setSslStep(n, state, msg) {
    // state: pending | run | ok | fail
    const el = document.querySelector(`.ssl-step[data-step="${n}"]`);
    if (!el) return;
    el.classList.remove("ssl-step--run", "ssl-step--ok", "ssl-step--fail");
    const icon = el.querySelector(".ssl-step__icon");
    if (state === "run") {
      el.classList.add("ssl-step--run");
      if (icon) icon.textContent = "…";
    } else if (state === "ok") {
      el.classList.add("ssl-step--ok");
      if (icon) icon.textContent = "✓";
    } else if (state === "fail") {
      el.classList.add("ssl-step--fail");
      if (icon) icon.textContent = "✕";
    } else {
      if (icon) icon.textContent = "○";
    }
    if (msg != null && $("ssl-progress-msg")) {
      $("ssl-progress-msg").textContent = msg;
    }
  }

  function resetSslSteps() {
    for (let i = 1; i <= 4; i++) setSslStep(i, "pending");
    if ($("ssl-progress-msg")) $("ssl-progress-msg").textContent = "";
  }

  async function save(btn) {
    if (btn) btn.disabled = true;
    try {
      const data = await panel.post(path("api_settings") + "/panel", readPayload());
      applyStatus(data);
      showNotes(data.notes || ["Saved."], "success");
      toast("Saved", "success");
    } catch (err) {
      showNotes([err.message || "Save failed"], "danger");
      toast(err.message || "Save failed", "danger");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function issueSsl(btn) {
    const host = computedHostname();
    if (!host) {
      toast("Set a hostname first", "danger");
      return;
    }
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Issuing…";
    }
    showSslProgress(true);
    resetSslSteps();

    try {
      // 1 — save hostname
      setSslStep(1, "run", "Saving hostname…");
      await panel.post(path("api_settings") + "/panel", readPayload());
      setSslStep(1, "ok", "Hostname saved.");

      // 2 — prepare nginx
      setSslStep(2, "run", "Preparing nginx…");
      const prep = await panel.post(path("api_settings") + "/panel/ssl/prepare", {});
      setSslStep(2, "ok", prep.message || "Nginx ready.");

      // 3 — certbot (slow)
      setSslStep(3, "run", "Requesting certificate from Let’s Encrypt (can take 1–2 minutes)…");
      const cert = await panel.post(path("api_settings") + "/panel/ssl/cert", {});
      setSslStep(3, "ok", cert.message || "Certificate issued.");

      // 4 — enable HTTPS
      setSslStep(4, "run", "Enabling HTTPS…");
      const done = await panel.post(path("api_settings") + "/panel/ssl/apply", {});
      setSslStep(4, "ok", done.message || "HTTPS enabled.");

      applyStatus(done);
      showNotes(done.notes || [done.message || "SSL done"], "success");
      toast("Panel SSL ready", "success");
    } catch (err) {
      const msg = err.message || "SSL failed";
      // mark first running step as fail
      for (let i = 1; i <= 4; i++) {
        const el = document.querySelector(`.ssl-step[data-step="${i}"]`);
        if (el && el.classList.contains("ssl-step--run")) {
          setSslStep(i, "fail", msg);
          break;
        }
      }
      if ($("ssl-progress-msg")) $("ssl-progress-msg").textContent = msg;
      showNotes([msg], "danger");
      toast(msg.length > 100 ? msg.slice(0, 100) + "…" : msg, "danger");
    } finally {
      if (btn) {
        btn.disabled = !computedHostname();
        btn.textContent = "Issue / renew SSL";
      }
    }
  }

  async function refresh() {
    try {
      applyStatus(await panel.get(path("api_settings")));
      toast("Refreshed", "success");
    } catch (err) {
      toast(err.message || "Refresh failed", "danger");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll('input[name="url_mode"]').forEach((el) => {
      el.addEventListener("change", syncUrlModeUi);
    });
    $("custom_domain")?.addEventListener("input", syncUrlModeUi);
    $("subdomain_label")?.addEventListener("input", syncUrlModeUi);
    $("parent_domain")?.addEventListener("change", syncUrlModeUi);
    $("allow_ip")?.addEventListener("change", syncUrlModeUi);

    $("btn-save-panel")?.addEventListener("click", (e) => save(e.currentTarget));
    $("btn-save-ip")?.addEventListener("click", (e) => save(e.currentTarget));
    $("btn-save-security")?.addEventListener("click", (e) => save(e.currentTarget));
    $("btn-issue-ssl")?.addEventListener("click", (e) => issueSsl(e.currentTarget));
    $("btn-refresh-settings")?.addEventListener("click", refresh);

    syncUrlModeUi();
  });
})();
