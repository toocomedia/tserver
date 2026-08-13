const API_ROOT = "/api/php-sites";

function detailText(detail) {
  if (!detail) return "Request failed.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  return String(detail);
}

function csrfHeaders(headers = {}) {
  return typeof window.csrfHeaders === "function" ? window.csrfHeaders(headers) : headers;
}

export async function request(path, method = "GET", body) {
  const options = { method, headers: csrfHeaders({ Accept: "application/json" }) };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(`${API_ROOT}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (response.status === 503) {
    window.location.assign("/dependencies");
    throw new Error(detailText(payload.detail));
  }
  if (!response.ok) throw new Error(detailText(payload.detail) || `Request failed (${response.status}).`);
  return payload;
}

export function t(key) {
  return typeof window._ === "function" ? window._(key) : key;
}

export function esc(value) {
  return String(value ?? "—").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
}

export function statusTone(status) {
  if (["active", "succeeded", "ready"].includes(status)) return "success";
  if (["degraded", "provisioning", "queued", "running"].includes(status)) return "warning";
  if (["failed", "deleting", "error"].includes(status)) return "danger";
  return "muted";
}

export async function waitForOperation(payload, onUpdate) {
  let operation;
  do {
    operation = await request(payload.status_url.replace(API_ROOT, ""));
    if (onUpdate) onUpdate(operation);
    if (!["queued", "running"].includes(operation.status)) return operation;
    await new Promise((resolve) => setTimeout(resolve, 750));
  } while (true);
}

export function actionUrl(siteId, suffix = "") {
  return `/sites/${encodeURIComponent(siteId)}${suffix}`;
}
