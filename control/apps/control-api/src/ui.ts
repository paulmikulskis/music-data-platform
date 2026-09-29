// Progressive enhancement: native forms retain their server-side behavior.
export const uiScript = `
// Only visiting the alert list advances its watermark. Storage may be disabled.
try{const key='mdp-alert-visit',previous=localStorage.getItem(key);document.querySelectorAll('[data-alert-changed]').forEach(dot=>{dot.hidden=!previous||Date.parse(dot.dataset.alertChanged)<=Date.parse(previous)});if(document.getElementById('alerts'))localStorage.setItem(key,new Date().toISOString())}catch{}
document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.querySelectorAll('.semantic-pop[open]').forEach(d=>{d.open=false;d.querySelector('summary').focus()})}});
function positionDetail(d){const panel=d.querySelector('.semantic-panel');if(innerWidth>650){const r=d.getBoundingClientRect();panel.style.position='fixed';panel.style.left=Math.max(12,Math.min(r.left,innerWidth-312))+'px';panel.style.top=Math.max(12,Math.min(r.bottom+6,innerHeight-panel.offsetHeight-12))+'px'}else panel.removeAttribute('style')}
document.addEventListener('toggle',e=>{const d=e.target;if(d.matches?.('.semantic-pop')&&d.open)positionDetail(d)},true);
window.addEventListener('resize',()=>document.querySelectorAll('.semantic-pop[open]').forEach(positionDetail));
function overflowHints(){document.querySelectorAll('.table-wrap').forEach(w=>{if(w.dataset.overflowHint==='off')return;let hint=w.previousElementSibling;if(hint?.classList.contains('table-guidance')){hint.querySelector('.overflow-guidance').textContent=w.scrollWidth>w.clientWidth+2?' · More columns →':'';return}if(!hint||!hint.classList.contains('table-hint')){hint=document.createElement('p');hint.className='table-hint';hint.textContent='More columns →';w.before(hint)}hint.classList.toggle('overflowing',w.scrollWidth>w.clientWidth+2)})}
function filterTable(table,term=''){
 const rows=[...table.querySelectorAll('[data-table-row]')];const matches=rows.filter(row=>row.textContent.toLowerCase().includes(term));
 rows.forEach(row=>row.hidden=true);matches.slice(0,term||table.dataset.expanded ? matches.length : Number(table.dataset.limit)).forEach(row=>row.hidden=false);
 const count=table.querySelector('[data-row-count]');if(count)count.textContent=term ? matches.length+' of '+rows.length+' rows match' : rows.length+' rows';
 let empty=table.querySelector('.zero-match');if(!empty){empty=document.createElement('tr');empty.className='zero-match';const cell=document.createElement('td');cell.className='empty';cell.colSpan=table.querySelectorAll('thead th').length;cell.textContent='No rows match this filter. Try another search or clear the filter.';empty.append(cell);table.querySelector('tbody').append(empty)}empty.hidden=matches.length>0;
 const show=table.querySelector('[data-show-all]');if(show)show.hidden=!!term||!!table.dataset.expanded;overflowHints();
}
document.addEventListener('click',async e=>{
 const budgets=e.target.closest('[data-all-budgets]');if(budgets){document.querySelectorAll('[data-budget-row]').forEach(row=>row.hidden=false);budgets.remove()}
 const more=e.target.closest('[data-show-alerts]');if(more){more.closest('details').querySelectorAll('[data-alert-row]').forEach(row=>row.hidden=false);more.remove()}
 const across=e.target.closest('[data-select-across]');if(across){const form=document.getElementById('bulk-targets');form.querySelectorAll('[data-across-id]').forEach(input=>input.remove());const ids=JSON.parse(across.dataset.selectAcross);document.querySelectorAll('[form=bulk-targets][name=ids]').forEach(box=>box.checked=true);const shown=new Set([...document.querySelectorAll('[form=bulk-targets][name=ids]')].map(box=>box.value));ids.filter(id=>!shown.has(id)).forEach(id=>{const input=document.createElement('input');input.type='hidden';input.name='ids';input.value=id;input.dataset.acrossId='true';form.append(input)});document.querySelector('[data-select-targets]').checked=true;updateSelection()}
 const resume=e.target.closest('[data-resume-draft]');if(resume)document.getElementById('import-csv')?.focus();
 const pause=e.target.closest('[data-pause-runs]');if(pause){const card=pause.closest('#recent-runs');const paused=card.dataset.paused!=='true';card.dataset.paused=String(paused);pause.setAttribute('aria-pressed',String(paused));pause.textContent=paused?'Resume polling':'Pause polling';card.querySelector('[role=status]').lastChild.textContent=paused?'live · paused':'live · refreshing'}
 const chip=e.target.closest('[data-copy-chip]');
 if(chip){try{await navigator.clipboard.writeText(chip.dataset.copyChip);chip.classList.add('copied');clearTimeout(chip._t);chip._t=setTimeout(()=>chip.classList.remove('copied'),1200)}catch{prompt('Copy value',chip.dataset.copyChip)}}
 const copy=e.target.closest('[data-copy]');
 if(copy){try{await navigator.clipboard.writeText(copy.dataset.copy);const original=copy.textContent;copy.textContent='Copied';setTimeout(()=>copy.textContent=original,1200)}catch{copy.textContent='Select to copy';prompt('Copy value',copy.dataset.copy)}}
 const dismiss=e.target.closest('[data-dismiss]');if(dismiss){dismiss.closest('.result,.error').remove();const u=new URL(location);u.searchParams.delete('result');history.replaceState(null,'',u)}
 const all=e.target.closest('[data-show-all]');if(all){const table=all.closest('[data-table]');table.dataset.expanded='true';table.querySelector('.table-wrap')?.classList.add('expanded-table');filterTable(table);}
 const receipt=e.target.closest('[data-open-receipts]');if(receipt){const group=document.getElementById('receipts-'+receipt.dataset.openReceipts);if(group){group.hidden=false;group.open=true;const filter=document.querySelector('[data-receipt-filter]');if(filter)filter.value='';document.querySelectorAll('[data-receipt-run]').forEach(g=>g.hidden=false)}}
});
function positionPopover(e){const host=e.target.closest?.('.pop');if(!host)return;const panel=host.querySelector('.pop-panel');if(!panel)return;const r=host.getBoundingClientRect();panel.style.visibility=r.bottom<0||r.top>innerHeight?'hidden':'';panel.style.position='fixed';panel.style.right='auto';panel.style.maxWidth='min(300px, calc(100vw - 16px))';panel.style.left=Math.max(8,Math.min(r.left,innerWidth-panel.offsetWidth-8))+'px';panel.style.top=(r.bottom+panel.offsetHeight+8>innerHeight?Math.max(8,r.top-panel.offsetHeight-6):r.bottom+6)+'px'}
document.addEventListener('pointerover',positionPopover);document.addEventListener('focusin',positionPopover);
function resetPopover(e){const host=e.target.closest?.('.pop');if(host)setTimeout(()=>{if(!host.matches(':hover,:focus-within'))host.querySelector('.pop-panel')?.removeAttribute('style')},0)}
document.addEventListener('pointerout',resetPopover);document.addEventListener('focusout',resetPopover);
function syncPopovers(){document.querySelectorAll('.pop:hover,.pop:focus-within').forEach(target=>positionPopover({target}))}
document.addEventListener('scroll',syncPopovers,true);window.addEventListener('resize',syncPopovers);
function hydrateTimes(){document.querySelectorAll('time[data-relative]').forEach(t=>{const m=Math.floor((Date.now()-Date.parse(t.getAttribute('datetime')))/60000);t.textContent=m<0?'in '+Math.max(1,-m)+'m':m<1?'just now':m<60?m+'m ago':m<1440?Math.floor(m/60)+'h ago':Math.floor(m/1440)+'d ago'})}
hydrateTimes();setInterval(hydrateTimes,60000);
document.addEventListener('submit',e=>{
 const form=e.target;const confirmation=form.dataset.confirm;
 if(confirmation&&!confirm(confirmation)){e.preventDefault();return}
 const button=e.submitter;if(button){button.style.width=button.getBoundingClientRect().width+'px';button.classList.add('pending-submit');button.textContent=button.dataset.pending||'Working…';setTimeout(()=>button.disabled=true,0)}
});
document.addEventListener('input',e=>{if(e.target.matches('[data-preview-filter]')){const table=e.target.closest('[data-preview]').querySelector('[data-table]');if(table)filterTable(table,e.target.value.toLowerCase())}});
function updateSelection(){const n=document.querySelectorAll('[form=bulk-targets][name=ids]:checked,#bulk-targets [data-across-id]').length;const button=document.querySelector('#bulk-targets button');if(button){button.textContent='Apply to '+n+' selected';button.disabled=n===0}}
document.addEventListener('change',e=>{
 if(e.target.matches('[data-select-targets],[form=bulk-targets][name=ids]'))document.querySelectorAll('[data-across-id]').forEach(input=>input.remove());
 if(e.target.matches('[data-select-targets]'))document.querySelectorAll('[form=bulk-targets][name=ids]').forEach(box=>box.checked=e.target.checked);
 if(e.target.matches('[form=bulk-targets][name=ids]')){const boxes=[...document.querySelectorAll('[form=bulk-targets][name=ids]')],all=document.querySelector('[data-select-targets]');all.checked=boxes.every(box=>box.checked);all.indeterminate=boxes.some(box=>box.checked)&&!all.checked}
 if(e.target.matches('[data-select-targets],[form=bulk-targets][name=ids]'))updateSelection();
 if(e.target.matches('[data-spark-window]'))e.target.closest('.card').querySelectorAll('[data-spark-range]').forEach(chart=>chart.hidden=chart.dataset.sparkRange!==e.target.value);
 if(e.target.matches('[data-column]')){e.target.closest('[data-preview]').querySelectorAll('table tr').forEach(row=>{if(!row.classList.contains('zero-match')&&row.children[e.target.dataset.column])row.children[e.target.dataset.column].hidden=!e.target.checked});overflowHints()}
 if(e.target.matches('[data-receipt-filter]'))document.querySelectorAll('[data-receipt-run]').forEach(group=>{group.hidden=!!e.target.value&&group.dataset.receiptRun!==e.target.value;if(e.target.value&&!group.hidden)group.open=true});
});
async function refreshRuns(){
 const card=document.getElementById('recent-runs');if(!card||!card.hasAttribute('data-live-run')||!card.dataset.liveUrl)return;
 if(card.dataset.paused==='true'||document.hidden||card.contains?.(document.activeElement)){setTimeout(refreshRuns,4000);return}
 const windowChoice=card.querySelector('[data-spark-window]')?.value;
 const active=[...card.querySelectorAll('[data-run-id]')].filter(row=>['queued','running','draining'].includes(row.dataset.status)).map(row=>row.dataset.runId);
 try{const response=await fetch(card.dataset.liveUrl,{credentials:'same-origin'});if(response.ok){const body=await response.text();if(card.dataset.paused==='true'){setTimeout(refreshRuns,4000);return}const doc=new DOMParser().parseFromString(body,'text/html');const next=doc.getElementById('recent-runs');if(next){next.classList.add('run-swap');
 for(const id of active){const row=[...doc.querySelectorAll('[data-run-id]')].find(row=>row.dataset.runId===id);if(row&&!['queued','running','draining'].includes(row.dataset.status)){const pill=document.createElement('a');pill.className='badge '+row.dataset.status;pill.setAttribute('role','status');pill.href='/runs/'+id;pill.dataset.openReceipts=id;pill.textContent=row.dataset.status==='succeeded'?'landed '+row.dataset.landed:row.dataset.status+' · '+row.dataset.rejected+' rejected';next.querySelector('.row').append(pill);setTimeout(()=>pill.remove(),6000)}}
 if(windowChoice){const select=next.querySelector('[data-spark-window]');if(select){select.value=windowChoice;next.querySelectorAll('[data-spark-range]').forEach(chart=>chart.hidden=chart.dataset.sparkRange!==windowChoice)}}
 card.replaceWith(next);
 }}}catch{const pill=card.querySelector('[role=status]');if(pill)pill.textContent='live · reconnecting'}
 overflowHints();setTimeout(refreshRuns,4000);
}
updateSelection();overflowHints();window.addEventListener('resize',overflowHints);setTimeout(refreshRuns,4000);

function placeFunctionMeta() {
  const meta = document.querySelector(".function-meta");
  if (!meta) return;
  const phone = matchMedia("(max-width:650px)").matches;
  document
    .querySelector(phone ? ".function-menu-body" : ".function-identity")
    ?.append(meta);
}
placeFunctionMeta();
window.addEventListener("resize", placeFunctionMeta);
let hoverTimer, hoverHost;
function hideHover() {
  clearTimeout(hoverTimer);
  document.getElementById("hover-card")?.remove();
  hoverHost?.removeAttribute("aria-describedby");
  hoverHost = null;
}
function openHover(host) {
  hideHover();
  hoverHost = host;
  host.setAttribute("aria-describedby", "hover-card");
  const card = document.createElement("div");
  card.id = "hover-card";
  card.setAttribute("role", "tooltip");
  const text = document.createElement("p");
  text.textContent = host.dataset.hover;
  card.append(text);
  const link = document.createElement("a");
  link.href = host.dataset.nextHref || host.getAttribute("href") || "/runs";
  link.textContent = host.dataset.nextLabel || "Open run →";
  card.append(link);
  const close = document.createElement("button");
  close.className = "secondary hover-close";
  close.textContent = "Close";
  close.onclick = hideHover;
  card.append(close);
  document.body.append(card);
  const r = host.getBoundingClientRect();
  card.style.left =
    Math.max(12, Math.min(r.left, innerWidth - card.offsetWidth - 12)) + "px";
  card.style.top =
    Math.max(12, Math.min(r.bottom + 8, innerHeight - card.offsetHeight - 12)) +
    "px";
}
window.addEventListener("resize", hideHover);
function scheduleHover(e) {
  const host = e.target.closest?.("[data-hover]");
  if (!host || hoverHost === host) return;
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(() => openHover(host), 150);
}
document.addEventListener("pointerenter", scheduleHover, true);
document.addEventListener("focusin", scheduleHover);
document.addEventListener(
  "pointerleave",
  (e) => {
    if (e.target === hoverHost && !matchMedia("(pointer:coarse)").matches) {
      clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => {
        if (
          !document
            .getElementById("hover-card")
            ?.matches(":hover,:focus-within")
        )
          hideHover();
      }, 200);
    }
  },
  true,
);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    hideHover();
    document.querySelectorAll(".function-menu[open]").forEach((d) => {
      d.open = false;
      d.querySelector("summary").focus();
    });
  }
  const grid = e.target.closest?.("[role=grid]");
  if (
    !grid ||
    ![
      "ArrowRight",
      "ArrowLeft",
      "ArrowUp",
      "ArrowDown",
      "Home",
      "End",
    ].includes(e.key)
  )
    return;
  const cells = [...grid.querySelectorAll("[role=gridcell]")];
  const i = cells.indexOf(e.target);
  if (i < 0) return;
  e.preventDefault();
  const next =
    e.key === "Home"
      ? 0
      : e.key === "End"
        ? cells.length - 1
        : (i +
            (["ArrowRight", "ArrowDown"].includes(e.key) ? 1 : -1) +
            cells.length) %
          cells.length;
  cells.forEach((c) => (c.tabIndex = -1));
  cells[next].tabIndex = 0;
  cells[next].focus();
});
function updateColumnCount(preview) {
  const boxes = [...preview.querySelectorAll("[data-column-name]")];
  const summary = preview.querySelector("[data-column-summary]");
  if (summary)
    summary.textContent =
      "Columns " +
      boxes.filter((b) => b.checked).length +
      " of " +
      boxes.length +
      " ▾";
}
function restoreColumns(root) {
  root
    .querySelectorAll("[data-preview][data-column-key]")
    .forEach((preview) => {
      try {
        const saved = JSON.parse(
          localStorage.getItem("mdp-columns:" + preview.dataset.columnKey) ||
            "null",
        );
        if (!Array.isArray(saved) || !saved.every((v) => typeof v === "string"))
          return;
        preview.querySelectorAll("[data-column-name]").forEach((box) => {
          box.checked = saved.includes(box.dataset.columnName);
        });
        preview
          .querySelectorAll("[data-col]")
          .forEach((cell) => (cell.hidden = !saved.includes(cell.dataset.col)));
        updateColumnCount(preview);
      } catch {}
    });
}
async function loadPart(host, url, label = "rows") {
  if (host.dataset.loading === "true") return;
  host.dataset.loading = "true";
  try {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw Error();
    const body = await response.text();
    host.innerHTML = body;
    host.dataset.loaded = "true";
    const disclosure = host.closest("[data-lazy]");
    if (disclosure) disclosure.dataset.loaded = "true";
    restoreColumns(host);
    hydrateTimes();
  } catch {
    host.replaceChildren();
    const text = document.createElement("p");
    text.textContent = "Couldn't load " + label + ". Try again.";
    const retry = document.createElement("button");
    retry.className = "secondary";
    retry.textContent = "Retry";
    retry.onclick = () => loadPart(host, url, label);
    const link = document.createElement("a");
    link.href =
      document
        .querySelector("#recent-runs [data-run-id]")
        ?.getAttribute("href") || "/runs";
    link.textContent = "Open run →";
    host.append(text, retry, link);
  } finally {
    delete host.dataset.loading;
  }
}
document.addEventListener(
  "toggle",
  (e) => {
    const d = e.target;
    if (d.matches?.("[data-lazy]") && d.open && d.dataset.loaded !== "true") {
      const body = d.querySelector("[data-lazy-body]");
      loadPart(body, d.dataset.lazy, d.dataset.part).then(() => {
        if (body.dataset.loaded === "true") d.dataset.loaded = "true";
      });
    }
  },
  true,
);
document.addEventListener("click", async (e) => {
  const tab = e.target.closest("[data-rows-tab]");
  if (tab) {
    const host = document.querySelector("[data-auto-part]");
    if (!host) return;
    e.preventDefault();
    document
      .querySelectorAll("[data-rows-tab]")
      .forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    await loadPart(host, tab.dataset.partUrl);
  }
  const filter = e.target.closest("[data-strip-filter]");
  if (filter) {
    e.preventDefault();
    hideHover();
    const dialog = document.getElementById("target-drill");
    dialog.showModal();
    await loadPart(
      dialog.querySelector("[data-drill-body]"),
      location.pathname +
        "/part/targets?state=" +
        encodeURIComponent(filter.dataset.stripFilter),
      "targets",
    );
  }
  const row = e.target.closest("[data-row-open]");
  if (row) document.getElementById(row.dataset.rowOpen)?.showModal();
  const all = e.target.closest("[data-target-all]");
  if (all) {
    all.closest("[data-target-list]").dataset.expanded = "true";
    all
      .closest("[data-target-list]")
      .querySelectorAll("[data-target-row]")
      .forEach((r) => (r.hidden = false));
    all.hidden = true;
  }
  const clear = e.target.closest("[data-clear-fn]");
  if (clear) {
    const input = document.querySelector("[data-fn-filter]");
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }
  const sort = e.target.closest("[data-sort]");
  if (sort) {
    const table = sort.closest("table"),
      col = [...sort.closest("tr").children].indexOf(sort.closest("th")),
      dir = sort.dataset.direction === "asc" ? -1 : 1;
    sort.dataset.direction = dir === 1 ? "asc" : "desc";
    const rows = [...table.tBodies[0].rows];
    rows.sort((a, b) => {
      const x = a.cells[col].dataset.sortValue,
        y = b.cells[col].dataset.sortValue;
      return (
        dir *
        (x !== "" &&
        y !== "" &&
        Number.isFinite(Number(x)) &&
        Number.isFinite(Number(y))
          ? Number(x) - Number(y)
          : x.localeCompare(y))
      );
    });
    table.tBodies[0].append(...rows);
    sort
      .closest("th")
      .setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
  }
});
document.addEventListener("input", (e) => {
  if (e.target.matches("[data-fn-filter]")) {
    const term = e.target.value.toLowerCase();
    document
      .querySelectorAll("[data-fn-card]")
      .forEach((c) => (c.hidden = !c.textContent.toLowerCase().includes(term)));
    document.querySelectorAll("[data-fn-group]").forEach((g) => {
      g.hidden =
        !!term &&
        ![...g.querySelectorAll("[data-fn-card]")].some((c) => !c.hidden);
      if (g.tagName === "DETAILS" && term && !g.hidden) g.open = true;
    });
    document.querySelector("[data-fn-empty]").hidden = [
      ...document.querySelectorAll("[data-fn-card]"),
    ].some((c) => !c.hidden);
  }
  if (e.target.matches("[data-target-filter]")) {
    const list = e.target.closest("[data-target-list]"),
      term = e.target.value.toLowerCase();
    let n = 0;
    list.querySelectorAll("[data-target-row]").forEach((row) => {
      const match = row.textContent.toLowerCase().includes(term);
      row.hidden = !match || (!term && !list.dataset.expanded && n >= 25);
      if (match) n++;
    });
  }
});
document.addEventListener("change", (e) => {
  if (e.target.matches("[data-target-state]")) {
    const select = e.target;
    const host = select.closest("[data-drill-body]") || select.closest("#targets");
    if (host) {
      loadPart(
        host,
        select.dataset.partUrl + "?state=" + encodeURIComponent(select.value),
        "targets",
      );
    }
  }
  if (e.target.matches("[data-column-name]")) {
    const preview = e.target.closest("[data-preview]");
    const selected = [
      ...preview.querySelectorAll("[data-column-name]:checked"),
    ].map((b) => b.dataset.columnName);
    preview
      .querySelectorAll("[data-col]")
      .forEach((cell) => (cell.hidden = !selected.includes(cell.dataset.col)));
    updateColumnCount(preview);
    try {
      localStorage.setItem(
        "mdp-columns:" + preview.dataset.columnKey,
        JSON.stringify(selected),
      );
    } catch {}
  }
});
document.querySelectorAll("[data-auto-part]").forEach((host) => {
  if (host.dataset.loaded !== "true") loadPart(host, host.dataset.autoPart);
});
restoreColumns(document);
`;
