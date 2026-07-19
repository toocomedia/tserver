/**
 * settings.js — Panel URL, IP access, SSL, security options
 */
(function () {
  function $(id) {
    return document.getElementById(id);
  }

  function readPayload() {
    return {
      panel_domain: ($("panel_domain")?.value || "").trim(),
      allow_ip: !!$("allow_ip")?.checked,
      ip_port: parseInt($("ip_port")?.value || "80", 10),
      ensure_dns: !!$("ensure_dns")?.checked,
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
    box.innerHTML = "<ul style='margin:0;padding-left:1.2em'>" +
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

  function applyStatus(s) {
    if (!s) return;
    if ($("panel_domain") && document.activeElement !== $("panel_domain")) {
      $("panel_domain").value = s.panel_domain || "";
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
    if ($("btn-issue-ssl")) $("btn-issue-ssl").disabled = !s.panel_domain;

    if ($("stat-ssl")) {
      $("stat-ssl").innerHTML = s.ssl_active
        ? '<span class="badge badge--ok">active</span>'
        : '<span class="badge badge--neutral">off</span>';
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
        links.push(`<a href="${s.urls.ip_http}" target="_blank" rel="noopener">${s.urls.ip_http}</a>`);
      }
      if (s.urls.domain_http) {
        links.push(`<a href="${s.urls.domain_http}" target="_blank" rel="noopener">${s.urls.domain_http}</a>`);
      }
      if (s.urls.domain_https) {
        links.push(`<a href="${s.urls.domain_https}" target="_blank" rel="noopener">${s.urls.domain_https}</a>`);
      }
      $("stat-urls").innerHTML = links.join("<br>") || '<span class="text-muted">—</span>';
    }
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
      // Persist hostname first if edited
      await panel.post("/api/settings/panel", readPayload());
      const data = await panel.post("/api/settings/panel/ssl", {});
      applyStatus(data);
      showNotes(data.notes || ["SSL issued."]);
      toast("Panel SSL issued", "success");
    } catch (err) {
      toast(err.message || "SSL failed", "danger");
    } finally {
      if (btn) btn.disabled = !$("panel_domain")?.value?.trim();
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

    $("panel_domain")?.addEventListener("input", () => {
      if ($("btn-issue-ssl")) {
        $("btn-issue-ssl").disabled = !($("panel_domain").value || "").trim();
      }
    });
  });
})();
