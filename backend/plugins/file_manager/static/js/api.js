export function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

async function handleResponse(res) {
  if (res.status === 413) {
    const error = new Error('Uploads are limited to 100 MB. Use SFTP for larger files.');
    error.status = 413;
    throw error;
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const error = new Error(data?.detail || `API Error: ${res.status}`);
    error.status = res.status;
    error.data = data;
    throw error;
  }
  return data;
}

export async function fetchApps() {
  const res = await fetch('/plugins/file_manager/api/apps');
  return handleResponse(res);
}

export async function fetchRoots(appId) {
  const res = await fetch(`/plugins/file_manager/api/apps/${encodeURIComponent(appId)}/roots`);
  return handleResponse(res);
}

export async function fetchEntries(appId, rootId, path = '') {
  const res = await fetch(`/plugins/file_manager/api/apps/${encodeURIComponent(appId)}/roots/${encodeURIComponent(rootId)}/entries?path=${encodeURIComponent(path)}`);
  return handleResponse(res);
}

export async function fetchText(appId, rootId, path) {
  const res = await fetch(`/plugins/file_manager/api/apps/${encodeURIComponent(appId)}/roots/${encodeURIComponent(rootId)}/text?path=${encodeURIComponent(path)}`);
  return handleResponse(res);
}

export async function saveText(appId, rootId, path, content, etag) {
  const body = { path, content };
  if (etag) body.etag = etag;
  const res = await fetch(`/plugins/file_manager/api/apps/${encodeURIComponent(appId)}/roots/${encodeURIComponent(rootId)}/text`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': getCsrfToken()
    },
    body: JSON.stringify(body)
  });
  return handleResponse(res);
}

export async function createDirectory(appId, rootId, path) {
  const res = await fetch(`/plugins/file_manager/api/apps/${encodeURIComponent(appId)}/roots/${encodeURIComponent(rootId)}/directories`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': getCsrfToken()
    },
    body: JSON.stringify({ path })
  });
  return handleResponse(res);
}

export async function uploadFile(appId, rootId, path, file, etag = null) {
  const formData = new FormData();
  formData.append('path', path);
  formData.append('file', file);
  if (etag) formData.append('etag', etag);
  
  const res = await fetch(`/plugins/file_manager/api/apps/${encodeURIComponent(appId)}/roots/${encodeURIComponent(rootId)}/upload`, {
    method: 'POST',
    headers: {
      'X-CSRF-Token': getCsrfToken()
    },
    body: formData
  });
  return handleResponse(res);
}

export async function transferFile(appId, rootId, action, source_path, destination_path) {
  const endpoint = action === 'copy' ? 'copy' : 'move';
  const res = await fetch(`/plugins/file_manager/api/apps/${encodeURIComponent(appId)}/roots/${encodeURIComponent(rootId)}/${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': getCsrfToken()
    },
    body: JSON.stringify({ source_path, destination_path })
  });
  return handleResponse(res);
}

export async function deleteFile(appId, rootId, path, confirmation) {
  const res = await fetch(`/plugins/file_manager/api/apps/${encodeURIComponent(appId)}/roots/${encodeURIComponent(rootId)}/entries`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': getCsrfToken()
    },
    body: JSON.stringify({ path, confirmation })
  });
  return handleResponse(res);
}
