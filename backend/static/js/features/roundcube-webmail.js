(() => {
  const page = document.getElementById('roundcube-page');
  if (!page) return;
  const domain = page.dataset.domain || '';
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  let pollTimer = null;

  const activateTab = (name, updateUrl = true) => {
    const target = Array.from(document.querySelectorAll('.roundcube-tab-panel'))
      .find((panel) => panel.dataset.panel === name);
    if (!target) return;
    document.querySelectorAll('.roundcube-tab-button').forEach((button) => {
      const active = button.dataset.tab === name;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    document.querySelectorAll('.roundcube-tab-panel').forEach((panel) => {
      panel.classList.toggle('active', panel === target);
    });
    if (updateUrl) {
      history.replaceState(null, '', `${location.pathname}${location.search}#${name}`);
    }
  };

  document.querySelectorAll('.roundcube-tab-button').forEach((button) => {
    button.addEventListener('click', () => activateTab(button.dataset.tab));
  });
  if (location.hash) activateTab(location.hash.slice(1), false);

  const readJson = async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || 'Request failed.');
    return data;
  };

  const showResult = (id, message, success = false) => {
    const element = document.getElementById(id);
    if (!element) return;
    element.style.display = 'block';
    element.className = `text-small ${success ? 'text-accent' : 'text-danger'}`;
    element.textContent = message;
  };

  const setBadge = (id, text, ready) => {
    const element = document.getElementById(id);
    if (!element) return;
    const badge = document.createElement('span');
    badge.className = `badge ${ready ? 'badge--success' : 'badge--inactive'}`;
    badge.textContent = text;
    element.replaceChildren(badge);
  };

  const renderStatus = (data) => {
    const container = data.container || {};
    const site = data.site;
    setBadge('container-status', container.healthy ? 'Healthy' : (container.state || 'Stopped'), container.healthy);
    if (!site) return;
    const dns = site.dns || {};
    const dnsText = dns.status === 'ready' ? 'Ready'
      : dns.status === 'mismatch' ? 'Wrong address'
        : dns.status === 'pending' ? 'Not resolved' : 'Not configured';
    setBadge('dns-status', dnsText, dns.status === 'ready');
    const sslReady = site.ssl_status === 'ready';
    const sslText = sslReady ? 'Active' : site.ssl_status === 'pending'
      ? 'Issuing…' : site.ssl_status === 'error' ? 'Failed' : 'Not configured';
    setBadge('ssl-status', sslText, sslReady);

    const message = document.getElementById('status-message');
    if (message) {
      message.textContent = site.ssl_error
        || (sslReady ? `${site.public_url} is ready.`
          : dns.status === 'ready' ? 'DNS is ready. Issue HTTPS next.'
            : 'Save the hostname and wait for its A record to resolve.');
      message.className = `text-small ${site.ssl_error ? 'text-danger' : 'text-muted'}`;
    }
    const detailBox = document.getElementById('ssl-error-details');
    const detail = document.getElementById('ssl-error-detail');
    if (detailBox && detail) {
      detail.textContent = site.ssl_error_detail || '';
      detailBox.style.display = site.ssl_error_detail ? '' : 'none';
    }
    const open = document.getElementById('open-webmail-button');
    if (open && site.public_url && container.healthy) {
      if (open.tagName === 'A') {
        open.href = site.public_url;
      } else {
        open.onclick = () => window.open(site.public_url, '_blank', 'noopener');
      }
      open.disabled = false;
      open.title = '';
    }
    const sslButton = document.getElementById('issue-ssl-button');
    if (sslButton) sslButton.disabled = site.ssl_status === 'pending';
    const reload = document.getElementById('reload-roundcube-button');
    if (reload) {
      reload.disabled = data.rebuild_status === 'pending';
      reload.textContent = data.rebuild_status === 'pending' ? 'Detecting…' : 'Detect Mail Security';
    }
    if (pollTimer) clearTimeout(pollTimer);
    if (site.ssl_status === 'pending' || data.rebuild_status === 'pending') {
      pollTimer = setTimeout(refreshStatus, 2000);
    }
  };

  async function refreshStatus() {
    if (!domain || page.dataset.siteConfigured !== 'true') return;
    try {
      const response = await fetch(`/plugins/roundcube_webmail/api/status?domain=${encodeURIComponent(domain)}`);
      renderStatus(await readJson(response));
    } catch (error) {
      showResult('site-result', error.message);
    }
  }

  const manageDns = document.getElementById('manage-dns');
  const hostInput = document.getElementById('public-host');
  const externalRecord = document.getElementById('external-dns-record');
  const syncDnsPreview = () => {
    if (externalRecord) externalRecord.style.display = manageDns?.checked ? 'none' : '';
    const host = document.getElementById('dns-host-preview');
    if (host) host.textContent = hostInput?.value || '';
  };
  manageDns?.addEventListener('change', syncDnsPreview);
  hostInput?.addEventListener('input', syncDnsPreview);
  syncDnsPreview();

  document.getElementById('copy-dns-button')?.addEventListener('click', async () => {
    const host = hostInput?.value || '';
    const target = document.getElementById('dns-target')?.textContent || '';
    await navigator.clipboard.writeText(`A ${host} ${target}`);
  });

  const saveSite = async (form, confirmedHostChange = false) => {
    const button = document.getElementById('save-site-button');
    button.disabled = true;
    button.textContent = 'Saving…';
    try {
      const body = new FormData(form);
      if (confirmedHostChange) body.set('confirm_host_change', 'true');
      const response = await fetch(form.action, { method: 'POST', body });
      const data = await readJson(response);
      showResult('site-result', data.message, true);
      window.location.assign(`/plugins/roundcube_webmail/?domain=${encodeURIComponent(domain)}`);
    } catch (error) {
      showResult('site-result', error.message);
      button.disabled = false;
      button.textContent = page.dataset.siteConfigured === 'true' ? 'Save Changes' : 'Add Webmail Access';
    }
  };

  document.getElementById('webmail-site-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const oldHost = (page.dataset.publicHost || '').trim().toLowerCase();
    const newHost = (hostInput?.value || '').trim().toLowerCase();
    const changed = page.dataset.siteConfigured === 'true' && oldHost && oldHost !== newHost;
    if (!changed) {
      saveSite(form);
      return;
    }
    const message = `Change ${oldHost} to ${newHost}? The SSL certificate and proxy for ${oldHost} will be removed. Its A record will also be deleted when DNS is managed by this panel.`;
    const proceed = () => saveSite(form, true);
    if (typeof confirmAction === 'function') {
      confirmAction(message, proceed, {
        title: 'Change webmail hostname?',
        okLabel: 'Change hostname',
        danger: true
      });
    } else if (window.confirm(message)) {
      proceed();
    }
  });

  document.getElementById('refresh-status-button')?.addEventListener('click', refreshStatus);

  document.getElementById('issue-ssl-button')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const body = new FormData();
      body.append('csrf_token', csrf);
      body.append('mail_domain', domain);
      const response = await fetch('/plugins/roundcube_webmail/api/sites/ssl', { method: 'POST', body });
      const data = await readJson(response);
      showResult('ssl-result', data.message, true);
      await refreshStatus();
    } catch (error) {
      showResult('ssl-result', error.message);
      button.disabled = false;
    }
  });

  document.getElementById('test-mail-button')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = 'Testing…';
    try {
      const response = await fetch('/plugins/roundcube_webmail/api/mail-diagnostics');
      const data = await readJson(response);
      const secure = data.transport !== 'local';
      showResult(
        'mail-test-result',
        `IMAP ${data.imap.host}:${data.imap.port}; SMTP ${data.smtp.host}:${data.smtp.port}. ${secure ? 'Encrypted.' : 'Local gateway.'}`,
        true
      );
    } catch (error) {
      showResult('mail-test-result', error.message);
    } finally {
      button.disabled = false;
      button.textContent = 'Test Maddy Connection';
    }
  });

  document.getElementById('reload-roundcube-button')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = 'Detecting…';
    try {
      const response = await fetch('/plugins/roundcube_webmail/api/reload', {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf, Accept: 'application/json' }
      });
      const data = await readJson(response);
      showResult('mail-test-result', data.message, true);
      await refreshStatus();
    } catch (error) {
      showResult('mail-test-result', error.message);
      button.disabled = false;
      button.textContent = 'Detect Mail Security';
    }
  });

  document.getElementById('delete-site-button')?.addEventListener('click', async (event) => {
    const confirmation = document.getElementById('delete-confirmation')?.value || '';
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = 'Deleting…';
    const body = new FormData();
    body.append('csrf_token', csrf);
    body.append('mail_domain', domain);
    body.append('confirmation', confirmation);
    try {
      const response = await fetch('/plugins/roundcube_webmail/api/sites/delete', { method: 'POST', body });
      await readJson(response);
      window.location.assign('/plugins/roundcube_webmail/');
    } catch (error) {
      showResult('delete-result', error.message);
      button.disabled = false;
      button.textContent = 'Delete Webmail Access';
    }
  });

  refreshStatus();
})();
