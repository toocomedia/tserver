/**
 * settings.js — Panel hostname modes, IP access, SSL, security
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

  function syncChoiceCards() {
    document.querySelectorAll(".settings-choice").forEach((card) => {
      const radio = card.querySelector('input[type="radio"]');
      card.classList.toggle("settings-choice--active", !!(radio && radio.checked));
    });
  }

  function syncUrlModeUi() {
    const mode = selectedUrlMode();
    const customBox = $("url-custom-fields");
    const subBox = $("url-subdomain-fields");
    if (customBox) customBox.hidden = mode !== "custom";
    if (subBox) subBox.hidden = mode !== "subdomain";

    const host = computedHostname();
    if ($("result-hostname")) {
      $("result-hostname").textContent = host || "(IP only)";
    }
    if ($("btn-issue-ssl")) {
      $("btn-issue-ssl").disabled = !host;
    }
    if ($("custom-dns-hint") && mode === "custom") {
      const ip = $("stat-server-ip")?.textContent || "SERVER_IP";
      $("custom-dns-hint").textContent = `A  ${host || "hostname"}  →  ${ip}`;
    }
    if (mode === "subdomain") {
      const label = ($("subdomain_label")?.value || "panel").trim().toLowerCase() || "panel";
      const parent = ($("parent_domain")?.value || "").trim().toLowerCase() || "example.com";
      if ($("subdomain-preview")) {
        $("subdomain-preview").textContent = `${label}.${parent}`;
      }
    }

    const portGroup = $("ip-port-group");
    if (portGroup && $("allow_ip")) {
      portGroup.style.opacity = $("allow_ip").checked ? "1" : "0.5";
    }

    syncChoiceCards();
  }

  function readPayload() {
    const mode = selectedUrlMode();
    return {
      url_mode: mode,
      custom_domain: ($("custom_domain")?.value || "").trim(),
      parent_domain: ($("parent_domain")?.value || "").trim(),
      subdomain_label: ($("subdomain_label")?.value || "panel").trim(),
      panel_domain: computedHostname(),
      allow_ip: !!$("allow_ip")?.checked,
      ip_port: parseInt($("ip_port")?.value || "80", 10),
      session_https_only: !!$("session_https_only")?.checked,
      security_headers: !!$("security_headers")?.checked,
      csrf_enabled: !!$("csrf_enabled")?.checked,
      hsts_enabled: !!$("hsts_enabled")?.checked,
      session_max_age_days: parseInt($("session_max_age_days")?.value || "7", 10),
    };
  }

  function showNotes(notes) {
    const box = $("settings-notes");
    if (!box) return;
    if (!notes || !notes.length) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.className = "alert alert--success mb-lg";
    box.innerHTML =
      "<ul style='margin:0;padding-left:1.2em'>" +
      notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("") +
      "</ul>";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
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
    const subRadio = $("url_mode_subdomain");
    if (subRadio) {
      subRadio.disabled = !(managed && managed.length);
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
    if ($("parent_domain") && s.parent_domain) {
      $("parent_domain").value = s.parent_domain;
    }

    if ($("allow_ip")) $("allow_ip").checked = !!s.allow_ip;
    if ($("ip_port") && document.activeElement !== $("ip_port")) {
      $("ip_port").value = s.ip_port || 80;
    }
    if ($("session_https_only")) $("session_https_only").checked = !!s.session_https_only;
    if ($("csrf_enabled")) $("csrf_enabled").checked = !!s.csrf_enabled;
    if ($("security_headers")) $("security_headers").checked = !!s.security_headers;
    if ($("hsts_enabled")) $("hsts_enabled").checked = !!s.hsts_enabled;
    if ($("session_max_age_days")) {
      $("session_max_age_days").value = s.session_max_age_days || 7;
    }
    if ($("stat-server-ip")) $("stat-server-ip").textContent = s.server_ip || "";
    if ($("stat-hostname")) {
      $("stat-hostname").textContent = s.panel_domain || "— (IP only)";
    }

    if ($("stat-ssl")) {
      $("stat-ssl").innerHTML = s.ssl_active
        ? '<span class="badge badge--ok">HTTPS active</span>'
        : '<span class="badge badge--neutral">HTTP only</span>';
    }
    if ($("stat-dns")) {
      if (!s.panel_domain) {
        $("stat-dns").innerHTML = '<span class="text-muted">—</span>';
      } else if (s.dns_ok === true) {
        $("stat-dns").innerHTML = '<span class="badge badge--ok">points here</span>';
      } else if (s.dns_ok === false) {
        $("stat-dns").innerHTML = '<span class="badge badge--error">not pointing here</span>';
      } else {
        $("stat-dns").innerHTML = '<span class="badge badge--neutral">unknown</span>';
      }
    }
    if ($("stat-urls") && s.urls) {
      const links = [];
      if (s.urls.ip_http) {
        links.push(
          `<a href="${s.urls.ip_http}" target="_blank" rel="noopener">${s.urls.ip_http}</a>`
        );
      }
      if (s.urls.domain_http) {
        links.push(
          `<a href="${s.urls.domain_http}" target="_blank" rel="noopener">${s.urls.domain_http}</a>`
        );
      }
      if (s.urls.domain_https) {
        links.push(
          `<a href="${s.urls.domain_https}" target="_blank" rel="noopener">${s.urls.domain_https}</a>`
        );
      }
      $("stat-urls").innerHTML = links.join("<br>") || '<span class="text-muted">—</span>';
    }

    syncUrlModeUi();
  }

  async function save(btn) {
    if (btn) btn.disabled = true;
    try {
      const data = await panel.post("/api/settings/panel", readPayload());
      applyStatus(data);
      showNotes(data.notes || ["Saved."]);
      toast("Settings saved", "success");
    } catch (err) {
      toast(err.message || "Save failed", "danger");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function issueSsl(btn) {
    if (btn) btn.disabled = true;
    try {
      await panel.post("/api/settings/panel", readPayload());
      const data = await panel.post("/api/settings/panel/ssl", {});
      applyStatus(data);
      showNotes(data.notes || ["SSL issued."]);
      toast("Panel SSL issued", "success");
    } catch (err) {
      toast(err.message || "SSL failed", "danger");
    } finally {
      if (btn) btn.disabled = !computedHostname();
    }
  }

  async function refresh() {
    try {
      const data = await panel.get("/api/settings");
      applyStatus(data);
      if (data.csrf_token) {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) meta.setAttribute("content", data.csrf_token);
      }
      toast("Status refreshed", "success");
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
    $("btn-issue-ssl")?.addEventListener("click", (e) => {
      confirmAction(
        "Issue a Let's Encrypt certificate for the panel hostname? DNS must already point to this server.",
        () => issueSsl(e.currentTarget)
      );
    });
    $("btn-refresh-settings")?.addEventListener("click", refresh);

    syncUrlModeUi();
  });
})();
