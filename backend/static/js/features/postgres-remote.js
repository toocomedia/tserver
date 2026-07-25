const base = "/plugins/postgres_manager/api";
const csrf = document.querySelector("meta[name='csrf-token']")?.content || "";
const form = document.getElementById("pg-remote-create-form");

if (form) {
  const error = document.getElementById("pg-remote-create-error");
  const views = document.querySelectorAll("[data-remote-view]");
  const steps = document.querySelectorAll("[data-remote-step]");
  const indicators = document.querySelectorAll("[data-remote-step-indicator]");
  const sourceInputs = document.querySelectorAll("input[name='remote-source']");
  let currentStep = 1;

  const field = id => document.getElementById(id);
  const source = () => document.querySelector("input[name='remote-source']:checked")?.value || "managed";
  const endpoint = () => source() === "managed"
    ? `${field("pg-remote-subdomain").value.trim()}.${field("pg-remote-domain").value}`
    : field("pg-remote-hostname").value.trim();
  const allowedCidrs = () => field("pg-remote-cidrs").value.split(/\r?\n|,/).map(value => value.trim()).filter(Boolean);
  const notify = (message, type = "success") => window.toast?.(message, type);
  const showError = message => { error.textContent = message; error.hidden = false; };
  const clearError = () => { error.hidden = true; error.textContent = ""; };

  function setView(name) {
    views.forEach(view => { view.hidden = view.dataset.remoteView !== name; });
    clearError();
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

  function setStep(step) {
    currentStep = step;
    steps.forEach(item => { item.hidden = Number(item.dataset.remoteStep) !== step; });
    indicators.forEach(item => item.classList.toggle("is-active", Number(item.dataset.remoteStepIndicator) === step));
    document.querySelector("[data-action='remote-previous']").hidden = step === 1;
    document.querySelector("[data-action='remote-next']").hidden = step === 3;
    field("pg-remote-enable").hidden = step !== 3;
    if (step === 3) updateReview();
    clearError();
  }

  function updateReview() {
    field("pg-remote-review-host").textContent = endpoint() || "—";
    field("pg-remote-review-source").textContent = source() === "managed" ? "Managed subdomain" : "External hostname";
    field("pg-remote-review-cidrs").textContent = allowedCidrs().join(", ") || "—";
  }

  function validateCurrentStep() {
    if (currentStep === 1) {
      const missing = source() === "managed"
        ? !field("pg-remote-domain").value || !field("pg-remote-subdomain").value.trim()
        : !field("pg-remote-hostname").value.trim();
      if (missing) showError("Enter a hostname before continuing.");
      return !missing;
    }
    if (currentStep === 2 && !allowedCidrs().length) {
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

  sourceInputs.forEach(input => input.addEventListener("change", updateSourceFields));
  field("pg-remote-domain").addEventListener("change", updatePreview);
  field("pg-remote-subdomain").addEventListener("input", updatePreview);
  document.addEventListener("click", event => {
    if (event.target.closest("[data-action='open-remote-create']")) setView("create");
    if (event.target.closest("[data-action='close-remote-create']")) setView("list");
    if (event.target.closest("[data-action='remote-previous']")) setStep(Math.max(1, currentStep - 1));
    if (event.target.closest("[data-action='remote-next']") && validateCurrentStep()) setStep(Math.min(3, currentStep + 1));
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!validateCurrentStep()) return;
    const button = field("pg-remote-enable");
    clearError(); button.disabled = true; button.classList.add("is-loading");
    const managed = source() === "managed";
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
      button.disabled = false; button.classList.remove("is-loading");
    }
  });

  document.addEventListener("click", async event => {
    const button = event.target.closest("[data-action='reissue-remote-ssl'], [data-action='test-remote-domain'], [data-action='delete-remote-domain']");
    if (!button) return;
    const domain = button.dataset.domain;
    if (!domain) return;
    if (button.dataset.action === "delete-remote-domain" && !window.confirm(`Delete remote endpoint '${domain}'?`)) return;
    button.disabled = true; button.classList.add("is-loading");
    try {
      if (button.dataset.action === "reissue-remote-ssl") await request(`/remote/domains/${encodeURIComponent(domain)}/ssl`, { method: "POST" });
      if (button.dataset.action === "test-remote-domain") await request(`/remote/domains/${encodeURIComponent(domain)}/test`, { method: "POST" });
      if (button.dataset.action === "delete-remote-domain") await request(`/remote/domains/${encodeURIComponent(domain)}`, { method: "DELETE" });
      notify(button.dataset.action === "test-remote-domain" ? "Endpoint is ready." : "Endpoint updated.");
      if (button.dataset.action !== "test-remote-domain") window.location.reload();
    } catch (exception) {
      notify(exception.message, "danger");
    } finally {
      button.disabled = false; button.classList.remove("is-loading");
    }
  });

  updateSourceFields();
  updatePreview();
  setStep(1);
}
