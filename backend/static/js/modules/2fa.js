document.addEventListener('app:init', () => {
  const btnSetup = document.getElementById('btn-setup-2fa');
  const btnDisable = document.getElementById('btn-disable-2fa');
  const btnVerify = document.getElementById('btn-verify-2fa');
  const btnCancel = document.getElementById('btn-cancel-2fa');
  
  const setupBox = document.getElementById('2fa-setup-box');
  const actionsBox = document.getElementById('2fa-actions');
  const qrImg = document.getElementById('2fa-qr-img');
  const secretText = document.getElementById('2fa-secret-text');
  const verifyCodeInput = document.getElementById('2fa-verify-code');

  if (btnSetup) {
    btnSetup.addEventListener('click', async () => {
      try {
        btnSetup.disabled = true;
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        const res = await fetch('/api/settings/2fa/setup', {
          method: 'POST',
          headers: {
            'Accept': 'application/json',
            'X-CSRFToken': csrfToken
          }
        });
        
        if (!res.ok) throw new Error('Failed to setup 2FA');
        const data = await res.json();
        
        if (data.error) throw new Error(data.error);
        
        qrImg.src = data.qr_uri;
        secretText.textContent = data.secret;
        
        actionsBox.style.display = 'none';
        setupBox.style.display = 'block';
      } catch (err) {
        alert(err.message);
      } finally {
        btnSetup.disabled = false;
      }
    });
  }

  if (btnCancel) {
    btnCancel.addEventListener('click', () => {
      setupBox.style.display = 'none';
      actionsBox.style.display = 'block';
      verifyCodeInput.value = '';
    });
  }

  if (btnVerify) {
    btnVerify.addEventListener('click', async () => {
      const code = verifyCodeInput.value.trim();
      if (!code) return alert('Please enter the 6-digit code');
      
      try {
        btnVerify.disabled = true;
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        const res = await fetch('/api/settings/2fa/verify', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-CSRFToken': csrfToken
          },
          body: JSON.stringify({ code })
        });
        
        const data = await res.json();
        if (data.error) {
          throw new Error(data.error);
        }
        
        // Success, reload page to show updated status
        window.location.reload();
      } catch (err) {
        alert(err.message);
        btnVerify.disabled = false;
      }
    });
  }

  if (btnDisable) {
    btnDisable.addEventListener('click', async () => {
      if (!confirm('Are you sure you want to disable Two-Factor Authentication?')) return;
      
      try {
        btnDisable.disabled = true;
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        const res = await fetch('/api/settings/2fa/disable', {
          method: 'POST',
          headers: {
            'Accept': 'application/json',
            'X-CSRFToken': csrfToken
          }
        });
        
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        
        window.location.reload();
      } catch (err) {
        alert(err.message);
        btnDisable.disabled = false;
      }
    });
  }
});
