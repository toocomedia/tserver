(() => {
  let inventory = [];
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
  const typeLabels = { build_dir: "Build dir", build_workspace: "Build workspace", dangling_image: "Dangling image", unused_image: "Unused image", build_cache: "Builder cache", old_log: "Old log" };

  function formatMb(v){ return `${Number(v||0).toFixed(1)} MB`; }
  function formatAge(it){ return it.age_days>0 ? `${Math.round(it.age_days)}d ago` : "—"; }

  function selected(){
    return inventory.filter((it)=> $(`disk-consumer-item-${it.item_id}`)?.checked);
  }

  function updateSelection(){
    const sel = selected();
    const selMb = sel.reduce((t,i)=> t+Number(i.size_mb||0),0);
    const recMb = inventory.filter((i)=> !i.protected).reduce((t,i)=> t+Number(i.size_mb||0),0);
    const totalEl = $("disk-consumers-total");
    if(totalEl) totalEl.textContent = `${formatMb(selMb)} selected of ${formatMb(recMb)} reclaimable`;
    const btn = $("btn-clean-disk-consumers");
    if(btn) btn.disabled = sel.length===0;
    const sa = $("disk-consumers-select-all");
    if(sa){
      const selectable = inventory.filter((i)=> !i.protected);
      sa.checked = selectable.length>0 && sel.length===selectable.length;
    }
  }

  function renderBreakdown(items){
    const breakdownEl = $("disk-consumers-breakdown");
    const badge = $("disk-consumers-badge");
    const bar = $("disk-consumers-bar");
    if(!breakdownEl) return;
    const rec = items.filter((i)=> !i.protected);
    const total = rec.reduce((t,i)=> t+Number(i.size_mb||0),0);
    if(!items.length){
      breakdownEl.textContent = "No reclaimable space found. You're all clean.";
      if(badge) badge.hidden=true;
      if(bar) bar.style.display="none";
      return;
    }
    const byType = (t)=> rec.filter((i)=> i.type===t).reduce((s,i)=> s+Number(i.size_mb||0),0);
    const bBuild = byType("build_dir");
    const bWs = byType("build_workspace");
    const bImg = byType("dangling_image")+byType("unused_image");
    const bCache = byType("build_cache");
    const bLogs = byType("old_log");
    const parts = [];
    if(bBuild) parts.push(`Build dirs ${formatMb(bBuild)}`);
    if(bWs) parts.push(`Workspaces ${formatMb(bWs)}`);
    if(bImg) parts.push(`Unused images ${formatMb(bImg)}`);
    if(bCache) parts.push(`Builder cache ${formatMb(bCache)}`);
    if(bLogs) parts.push(`Logs ${formatMb(bLogs)}`);
    breakdownEl.textContent = `${formatMb(total)} reclaimable — ${parts.join(" · ") || "select items below to free space"}`;
    if(badge){
      badge.textContent = formatMb(total);
      badge.hidden = total<1;
      badge.style.background = total>2048 ? "var(--color-danger)" : total>500 ? "var(--color-warn)" : "var(--color-accent)";
      badge.style.color = "#fff";
    }
    if(bar){
      bar.style.display = total>0 ? "block" : "none";
      const pct = (v)=> total? (v/total*100).toFixed(1)+"%" : "0%";
      const setBar=(id,v)=>{ const el=$(id); if(el) el.style.width=pct(v); };
      setBar("bar-consumers-build", bBuild);
      setBar("bar-consumers-workspace", bWs);
      setBar("bar-consumers-images", bImg);
      setBar("bar-consumers-cache", bCache);
      setBar("bar-consumers-logs", bLogs);
    }
  }

  function renderTable(items){
    inventory = items;
    const tbody = $("disk-consumers-tbody");
    if(!tbody) return;
    if(!items.length){
      tbody.innerHTML = '<tr><td colspan="6" class="text-muted">No reclaimable space found.</td></tr>';
      renderBreakdown(items);
      updateSelection();
      return;
    }
    // Sort: largest first, protected last
    const sorted = [...items].sort((a,b)=>{
      if(a.protected!==b.protected) return a.protected?1:-1;
      return (b.size_mb||0)-(a.size_mb||0);
    });
    tbody.innerHTML = sorted.map((it)=> `<tr>
      <td>${typeLabels[it.type]||escapeHtml(it.type)}</td>
      <td class="table-title" style="max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(it.path)}">${escapeHtml(it.path)}</td>
      <td>${formatMb(it.size_mb)}</td>
      <td>${formatAge(it)}</td>
      <td>${it.protected ? `<span class="status-badge status-badge--warning" title="${escapeHtml(it.protect_reason)}">Protected</span>` : "—"}</td>
      <td><input type="checkbox" id="disk-consumer-item-${escapeHtml(it.item_id)}" data-disk-consumer="${escapeHtml(it.item_id)}" ${it.protected?"disabled":""} aria-label="Select ${escapeHtml(it.path)}"></td>
    </tr>`).join("");
    renderBreakdown(items);
    updateSelection();
  }

  async function scan(){
    const btn = $("btn-scan-disk-consumers");
    const resultEl = $("disk-consumers-result");
    if(btn){ btn.disabled=true; btn.classList.add("is-loading"); btn.setAttribute("aria-busy","true"); }
    if(resultEl) resultEl.textContent="";
    try{
      const data = await panel.get("/api/resource-guard/disk-inventory");
      // General-safe: only show deletable (general) reclaimable space. Protected / plugin / dependency
      // images are hidden to avoid clutter and accidental deletion of panel needs.
      const items = data.deletable||[];
      const protCount = (data.protected||[]).length;
      renderTable(items);
      if(!items.length){
        const msg = protCount ? `No reclaimable space — ${protCount} protected item(s) kept for apps/plugins.` : "No reclaimable disk space found.";
        toast(msg, "info");
        if(resultEl && protCount) resultEl.textContent = `Kept ${protCount} protected item(s) for existing apps/plugins.`;
      } else if(protCount){
        if(resultEl) resultEl.textContent = `${protCount} protected item(s) kept (not shown).`;
      }
    }catch(e){
      toast(e.message||"Could not scan disk space","danger");
      const tbody=$("disk-consumers-tbody");
      if(tbody) tbody.innerHTML = `<tr><td colspan="6" class="text-muted">Scan failed: ${escapeHtml(e.message||"unknown error")}</td></tr>`;
    }finally{
      if(btn){ btn.disabled=false; btn.classList.remove("is-loading"); btn.removeAttribute("aria-busy"); }
    }
  }

  async function cleanup(){
    const btn = $("btn-clean-disk-consumers");
    const includeIds = selected().map((i)=> i.item_id);
    if(!includeIds.length) return;
    if(btn){ btn.disabled=true; btn.classList.add("is-loading"); btn.setAttribute("aria-busy","true"); }
    try{
      const res = await panel.post("/api/resource-guard/disk-cleanup", { include_ids: includeIds });
      const errs = res.errors?.length ? ` ${res.errors.length} item(s) skipped.` : "";
      const details = res.errors?.length ? ` Skipped: ${res.errors.slice(0,2).join(" | ")}${res.errors.length>2?" …":""}` : "";
      const el = $("disk-consumers-result");
      if(res.errors?.length){
        if(el) el.textContent = `Freed ${formatMb(res.freed_mb)}.${errs}${details}`;
        toast(res.errors[0]||`Some items could not be removed.${errs}`,"warning");
      }else{
        if(el) el.textContent = `Freed ${formatMb(res.freed_mb)}.${errs}`;
        toast(`Freed ${formatMb(res.freed_mb)}`,"success");
      }
      await scan();
      // Also refresh the mount bars via global fetchStats if available
      if(typeof fetchStats==="function") try{ fetchStats(true); }catch(_){}
    }catch(e){
      toast(e.message||"Could not free disk space","danger");
      const el=$("disk-consumers-result");
      if(el) el.textContent = e.message||"Cleanup failed";
    }finally{
      if(btn){ btn.classList.remove("is-loading"); btn.removeAttribute("aria-busy"); }
      updateSelection();
    }
  }

  async function pruneBuilder(){
    const btn = $("btn-prune-builder");
    if(btn){ btn.disabled=true; btn.classList.add("is-loading"); }
    try{
      const res = await panel.post("/api/resource-guard/builder-prune", {});
      const msg = res.freed_mb ? `Builder cache pruned: ${formatMb(res.freed_mb)} freed.` : "No builder cache to prune.";
      const el = $("disk-consumers-result");
      if(el) el.textContent = msg;
      toast(msg, res.freed_mb?"success":"info");
      await scan();
    }catch(e){
      toast(e.message||"Builder prune failed","danger");
    }finally{
      if(btn){ btn.disabled=false; btn.classList.remove("is-loading"); }
    }
  }

  document.addEventListener("app:init", ()=>{
    $("btn-scan-disk-consumers")?.addEventListener("click", scan);
    $("disk-consumers-tbody")?.addEventListener("change", updateSelection);
    $("disk-consumers-select-all")?.addEventListener("change", (ev)=>{
      inventory.filter((i)=> !i.protected).forEach((it)=>{
        const cb = $(`disk-consumer-item-${it.item_id}`);
        if(cb) cb.checked = ev.target.checked;
      });
      updateSelection();
    });
    $("btn-clean-disk-consumers")?.addEventListener("click", ()=>{
      const c = selected().length;
      if(!c) return;
      confirmAction(`Free disk space from ${c} selected item(s)? This cannot be undone.`, cleanup, { title:"Free Disk Space", okLabel:"Free selected space", danger:true });
    });
    $("btn-prune-builder")?.addEventListener("click", ()=>{
      confirmAction("Prune Docker builder cache? This frees BuildKit cache but does not delete active images.", pruneBuilder, { title:"Prune Builder Cache", okLabel:"Prune cache", danger:false });
    });
    // No auto-scan — user triggers via Scan button to avoid long initial load on slow VPS
    // Section stays idle until clicked; badge/bar remain hidden.
  });

  // Expose for manual refresh from usage inline script
  window.refreshDiskConsumers = scan;
})();
