const base = "/plugins/postgres_manager/api";
const csrf = document.querySelector("meta[name='csrf-token']")?.content || "";
const form = document.getElementById("pg-remote-create-form");

if (form) {
  const field = id => document.getElementById(id);
  const error = field("pg-remote-create-error");
  const views = document.querySelectorAll("[data-remote-view]");
  const source = () => document.querySelector("input[name='remote-source']:checked")?.value || "managed";
  const allowedCidrs = () => field("pg-remote-cidrs").value.split(/\r?\n|,/).map(value => value.trim()).filter(Boolean);
  const notify = (message, type = "success") => window.toast?.(message, type);

  function setView(name) {
    views.forEach(view => { view.hidden = view.dataset.remoteView !== name; });
    error.hidden = true;
  }

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  function updateSourceFields() {
    const managed = source() === "managed";
    field("pg-remote-managed-fields").hidden = !managed;
    field("pg-remote-external-fields").hidden = managed;
  }

  function updatePreview() {
    const domain = field("pg-remote-domain").value || "example.com";
    const subdomain = field("pg-remote-subdomain").value.trim() || "db";
    field("pg-remote-host-preview").textContent = `${subdomain}.${domain}`;
  }

  function validate() {
    const managed = source() === "managed";
    if (managed && (!field("pg-remote-domain").value || !field("pg-remote-subdomain").value.trim())) {
      showError("Select a parent domain and enter a subdomain label.");
      return false;
    }
    if (!managed && !field("pg-remote-hostname").value.trim()) {
      showError("Enter the external hostname that resolves to this VPS.");
      return false;
    }
    if (!allowedCidrs().length) {
      showError("Add at least one allowed client IP address or CIDR range.");
      return false;
    }
    return true;
  }

  async function request(path, options = {}) {
    const response = await fetch(base + path, {
      ...options,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, ...(options.headers || {}) },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
    return body;
  }

  document.querySelectorAll("input[name='remote-source']").forEach(input => input.addEventListener("change", updateSourceFields));
  field("pg-remote-domain").addEventListener("change", updatePreview);
  field("pg-remote-subdomain").addEventListener("input", updatePreview);
  document.addEventListener("click", event => {
    if (event.target.closest("[data-action='open-remote-create']")) setView("create");
    if (event.target.closest("[data-action='close-remote-create']")) setView("list");
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!validate()) return;
    const button = field("pg-remote-enable");
    const managed = source() === "managed";
    error.hidden = true;
    button.disabled = true;
    button.classList.add("is-loading");
    try {
      await request("/remote/domains", {
        method: "POST",
        body: JSON.stringify({
          mode: source(), domain: managed ? field("pg-remote-domain").value : null,
          subdomain: managed ? field("pg-remote-subdomain").value.trim() : null,
          hostname: managed ? null : field("pg-remote-hostname").value.trim(),
          issue_ssl: true, allowed_cidrs: allowedCidrs(),
        }),
      });
      notify("Remote endpoint enabled.");
      window.location.reload();
    } catch (exception) {
      showError(exception.message);
    } finally {
      button.disabled = false;
      button.classList.remove("is-loading");
    }
  });

  document.addEventListener("click", async event => {
    const button = event.target.closest("[data-action='reissue-remote-ssl'], [data-action='test-remote-domain'], [data-action='delete-remote-domain']");
    if (!button || !button.dataset.domain) return;
    const domain = button.dataset.domain;
    if (button.dataset.action === "delete-remote-domain" && !window.confirm(`Delete remote endpoint '${domain}'?`)) return;
    button.disabled = true;
    button.classList.add("is-loading");
    try {
      if (button.dataset.action === "reissue-remote-ssl") await request(`/remote/domains/${encodeURIComponent(domain)}/ssl`, { method: "POST" });
      if (button.dataset.action === "test-remote-domain") await request(`/remote/domains/${encodeURIComponent(domain)}/test`, { method: "POST" });
      if (button.dataset.action === "delete-remote-domain") await request(`/remote/domains/${encodeURIComponent(domain)}`, { method: "DELETE" });
      notify(button.dataset.action === "test-remote-domain" ? "Endpoint is ready." : "Endpoint updated.");
      if (button.dataset.action !== "test-remote-domain") window.location.reload();
    } catch (exception) {
      notify(exception.message, "danger");
    } finally {
      button.disabled = false;
      button.classList.remove("is-loading");
    }
  });

  updateSourceFields();
  updatePreview();
}
