// Presentation for the data explorer: tokens are inherited from the shared stylesheet
// (--surface,--muted,--line,--paper,--teal,--teal-dark,--danger,--warn,--radius,--card-radius).
// House rule honored: emphasis is a full border or a subtle fill, never a single-side rail.
import type { EntityKind } from "./explorer-data.js";

// Inline SVG glyphs, reused server-side (dangerouslySetInnerHTML) and client-side (innerHTML).
// table = cylinder · view = stacked · model = nodes · source = fetch (arrow into store).
export const GLYPHS: Record<EntityKind, string> = {
  table:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><ellipse cx="8" cy="3.2" rx="5" ry="2"/><path d="M3 3.2v9.6c0 1.1 2.2 2 5 2s5-.9 5-2V3.2M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/></svg>',
  view:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M8 2 14.5 5 8 8 1.5 5 8 2Z"/><path d="M1.5 8 8 11l6.5-3M1.5 11 8 14l6.5-3"/></svg>',
  model:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><rect x="1.5" y="5.5" width="5" height="5" rx="1"/><rect x="9.5" y="1.5" width="5" height="4.2" rx="1"/><rect x="9.5" y="9.5" width="5" height="4.2" rx="1"/><path d="M6.5 8H9.5M9.5 3.6H8v7.9h1.5"/></svg>',
  source:
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M8 1.5v7M5.2 6l2.8 2.8L10.8 6"/><path d="M2.5 10.5v2c0 .6.4 1 1 1h9c.6 0 1-.4 1-1v-2"/></svg>',
};
export const KIND_LABEL: Record<EntityKind, string> = {
  table: "Table",
  view: "View",
  model: "Model",
  source: "Function",
};

export const explorerCss = `
.ex{--qn-prefix:#8aa1a4;min-width:0}
.ex .ex-search{position:relative;margin:0 0 22px;max-width:640px}
.ex .ex-search input{width:100%;max-width:none;padding:12px 14px;font-size:15px}
.ex .ex-search .ex-hint{font-size:12px;color:var(--muted);margin:6px 2px 0}
.ex .ex-drop{position:absolute;z-index:40;left:0;right:0;top:calc(100% + 6px);background:white;border:1px solid var(--line);border-radius:var(--card-radius);box-shadow:0 12px 44px color-mix(in srgb,var(--foreground) 12%,transparent);max-height:min(70vh,540px);overflow:auto;padding:6px}
.ex .ex-drop[hidden]{display:none}
.ex .ex-drop-label{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);padding:12px 10px 6px;font-weight:700}
.ex .ex-result{display:grid;grid-template-columns:16px minmax(0,1fr) auto;gap:10px;align-items:center;padding:8px 10px;border-radius:var(--radius);color:var(--foreground);cursor:pointer}
.ex .ex-result:hover,.ex .ex-result[aria-selected=true]{background:color-mix(in srgb,var(--teal) 8%,white);text-decoration:none}
.ex .ex-result .ex-glyph{color:var(--muted)}
.ex .ex-result .ex-r-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.ex .ex-result .ex-r-sub{font-size:11px;color:var(--muted);font-family:ui-monospace,monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ex .ex-result .ex-r-kind{font-size:11px;color:var(--muted);white-space:nowrap}
.ex .ex-result mark{background:color-mix(in srgb,var(--teal) 22%,white);color:var(--teal-dark);border-radius:3px;padding:0 1px}
.ex .ex-empty-drop{padding:16px 12px;color:var(--muted);font-size:13px}

.ex .ex-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr));gap:20px;align-items:start}
.ex .ex-group{--ex-pad:20px;background:white;border:1px solid var(--line);border-radius:var(--card-radius);padding:var(--ex-pad);overflow:hidden}
.ex .ex-group>header{background:var(--teal-dark);color:#fff;margin:calc(var(--ex-pad)*-1) calc(var(--ex-pad)*-1) 14px;padding:13px var(--ex-pad);display:flex;flex-direction:column;justify-content:center;gap:3px;min-height:64px;box-sizing:border-box}
.ex .ex-group>header h2{margin:0;color:#fff;font-size:15px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ex .ex-group>header .ex-sub{margin:0;font-size:12px;color:color-mix(in srgb,#fff 66%,var(--teal-dark));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ex .ex-section{border-top:1px solid var(--line);padding:4px 0}
.ex .ex-section>summary{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 2px;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:700}
.ex .ex-section>summary::-webkit-details-marker{display:none}
.ex .ex-section>summary .ex-caret{transition:transform .15s ease-out;color:var(--muted)}
.ex .ex-section[open]>summary .ex-caret{transform:rotate(90deg)}
.ex .ex-section .ex-count{color:var(--muted);font-weight:600}
.ex .ex-row{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:10px;align-items:center;padding:9px 8px;border-radius:var(--radius);color:var(--foreground)}
.ex .ex-row:hover{background:color-mix(in srgb,var(--teal) 7%,white);text-decoration:none}
.ex .ex-row .ex-glyph{color:var(--muted);display:flex}
.ex .ex-row .ex-glyph svg{width:15px;height:15px}
.ex .ex-name{min-width:0}
.ex .ex-name .ex-title{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}
.ex .ex-name .ex-title.ex-title-slug{font-family:ui-monospace,SFMono-Regular,monospace;font-weight:550;font-size:13px;letter-spacing:-.2px;color:color-mix(in srgb,var(--foreground) 88%,var(--muted))}
.ex .ex-slug{font-size:11px;color:var(--muted);font-family:ui-monospace,monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;margin-top:1px}
.ex .ex-when{font-size:11px;color:var(--muted);white-space:nowrap;display:flex;align-items:center;gap:5px}
.ex .ex-when .ex-dot{width:6px;height:6px;border-radius:50%;background:var(--teal)}
.ex .ex-section .ex-empty{padding:8px;color:var(--muted);font-size:12px}

.ex .qn-prefix{color:var(--qn-prefix);font-weight:500}
.ex .qn-leaf{color:inherit;font-weight:inherit}

.ex .ex-copy{border:0;background:transparent;padding:0 4px;color:var(--muted);font-family:ui-monospace,monospace;font-size:11px;cursor:pointer;border-radius:4px}
.ex .ex-copy:hover{background:color-mix(in srgb,var(--teal) 10%,white);color:var(--teal);text-decoration:none}

.ex-pop{position:fixed;z-index:60;width:340px;max-width:calc(100vw - 24px);background:white;border:1px solid var(--line);border-radius:var(--card-radius);box-shadow:0 14px 46px color-mix(in srgb,var(--foreground) 16%,transparent);padding:16px;pointer-events:none;font-size:13px;animation:ex-pop-in .12s ease-out}
.ex-pop[hidden]{display:none}
.ex-pop .ex-pop-head{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.ex-pop .ex-pop-glyph{color:var(--teal)}
.ex-pop .ex-pop-glyph svg{width:16px;height:16px}
.ex-pop .ex-pop-title{font-weight:650;overflow-wrap:anywhere}
.ex-pop .ex-pop-kind{margin-left:auto;font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:var(--radius);padding:1px 7px}
.ex-pop .ex-pop-loc{color:var(--muted);font-size:12px;margin:0 0 8px;overflow-wrap:anywhere}
.ex-pop .ex-pop-desc{margin:0 0 10px;color:var(--foreground)}
.ex-pop .ex-pop-stats{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:9px}
.ex-pop .ex-pop-stats strong{color:var(--foreground);font-weight:650;font-variant-numeric:tabular-nums}
.ex-pop .ex-pop-cols{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}
.ex-pop .ex-chip{font-size:11px;font-family:ui-monospace,monospace;background:var(--paper);border:1px solid var(--line);border-radius:var(--radius);padding:2px 7px;color:var(--muted)}
.ex-pop .ex-pop-open{margin-top:10px;font-size:12px;color:var(--teal);font-weight:600}
@keyframes ex-pop-in{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}

/* entity view */
.ex-entity .ex-head{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;margin-bottom:4px}
.ex-entity .ex-head .ex-glyph{color:var(--teal);align-self:center}
.ex-entity .ex-head .ex-glyph svg{width:22px;height:22px}
.ex-entity .ex-loc{color:var(--muted);margin:0 0 18px}
.ex-entity .ex-stat-strip{display:flex;flex-wrap:wrap;gap:22px;margin:0 0 8px}
.ex-entity .ex-stat .ex-stat-value{font-size:26px;letter-spacing:-.6px;font-weight:650;font-variant-numeric:tabular-nums}
.ex-entity .ex-stat .ex-stat-label{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--muted)}
.ex-entity .ex-slug-line{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 6px}
.ex-entity .ex-schema-table th button{background:transparent;border:0;padding:0;color:inherit;cursor:default}
.ex-entity .ex-nullable-y{color:var(--muted)}
.ex-entity .ex-nullable-n{color:var(--foreground);font-weight:600}
.ex-entity .ex-col-filter{max-width:280px}
.ex-entity .ex-author{font-size:12px;color:var(--muted)}
.ex-entity .ex-author strong{color:var(--foreground);font-weight:600}

/* lineage — echoes the workbench Composition frame, no side rails */
.ex-lineage{border:1px solid color-mix(in srgb,var(--teal) 30%,var(--line));border-radius:var(--card-radius);padding:16px;background:var(--paper)}
.ex-lineage h3{margin:0 0 4px}
.ex-lineage .ex-lin-sub{font-size:12px;color:var(--muted);margin:0 0 12px}
.ex-lin-group+.ex-lin-group{margin-top:14px}
.ex-lin-group>span{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);font-weight:700}
.ex-nodes{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.ex-node{display:flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:10px;padding:8px 11px;background:white;color:var(--foreground);font-size:13px;min-width:0}
.ex-node.is-source{background:color-mix(in srgb,var(--teal) 8%,var(--paper))}
.ex-node:hover{border-color:var(--teal);text-decoration:none}
.ex-node .ex-glyph{color:var(--muted);display:flex}
.ex-node .ex-glyph svg{width:13px;height:13px}
.ex-node span{overflow-wrap:anywhere;min-width:0}
.ex-lineage .ex-lin-empty{color:var(--muted);font-size:13px;margin:8px 0 0}

.ex-notice{border:1px solid var(--line);background:var(--paper);border-radius:var(--radius);padding:10px 14px;color:var(--muted);font-size:13px;margin:0 0 18px}
.ex-crumbs{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 18px}
.ex-crumbs a{font-size:13px}
@media(max-width:650px){.ex .ex-group{padding:16px}.ex-pop{width:min(340px,calc(100vw - 24px))}}
@media(prefers-reduced-motion:reduce){.ex-pop,.ex .ex-section>summary .ex-caret{animation:none;transition:none}}
`;

// Vanilla progressive-enhancement client: typeahead, fuzzy column search, hover-to-preview.
// No framework, no CDN. Reads an inline JSON index; the tree + entity pages work without it.
export const explorerScript = `
(function(){
 var root=document.querySelector('.ex');if(!root)return;
 var dataEl=document.getElementById('ex-index');var index={entities:[],columns:[]};
 try{index=JSON.parse(dataEl.textContent)}catch(e){}
 var glyphs=${JSON.stringify(GLYPHS)};
 var kindLabel=${JSON.stringify(KIND_LABEL)};
 var byId={};index.entities.forEach(function(e){byId[e.id]=e});
 var input=root.querySelector('[data-ex-input]');
 var drop=root.querySelector('[data-ex-drop]');
 var pop=document.getElementById('ex-pop');
 var active=-1;var results=[];
 function esc(s){return String(s).replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
 function subseq(q,s){var i=0;s=s.toLowerCase();for(var j=0;j<s.length&&i<q.length;j++)if(s[j]===q[i])i++;return i===q.length}
 function score(q,hay){hay=hay.toLowerCase();var at=hay.indexOf(q);if(at===0)return 0;if(at>0)return 1;if(subseq(q,hay))return 2;return -1}
 function mark(text,q){var low=text.toLowerCase();var at=low.indexOf(q);if(at<0)return esc(text);return esc(text.slice(0,at))+'<mark>'+esc(text.slice(at,at+q.length))+'</mark>'+esc(text.slice(at+q.length))}
 function search(q){
  q=q.trim().toLowerCase();if(!q)return null;
  var ents=[];
  index.entities.forEach(function(e){
   var s=Math.min.apply(null,[score(q,e.display),score(q,e.slug),score(q,e.qualified)].map(function(v){return v<0?9:v}));
   if(s<9)ents.push({e:e,s:s})
  });
  ents.sort(function(a,b){return a.s-b.s||a.e.display.localeCompare(b.e.display)});
  var seen={};var cols=[];
  index.columns.forEach(function(c){
   var s=score(q,c.column);if(s<0)return;var key=c.column+'|'+c.id;if(seen[key])return;seen[key]=1;cols.push({c:c,s:s})
  });
  cols.sort(function(a,b){return a.s-b.s||a.c.column.localeCompare(b.c.column)});
  return {ents:ents.slice(0,10).map(function(x){return x.e}),cols:cols.slice(0,8).map(function(x){return x.c}),q:q}
 }
 function render(res){
  results=[];if(!res){drop.hidden=true;drop.innerHTML='';return}
  var html='';
  if(res.ents.length){html+='<div class="ex-drop-label">Entities</div>';res.ents.forEach(function(e){
   results.push({id:e.id,href:e.href});
   html+='<a class="ex-result" href="'+e.href+'" data-result-id="'+esc(e.id)+'"><span class="ex-glyph">'+glyphs[e.kind]+'</span><span class="ex-r-name">'+mark(e.display,res.q)+'<span class="ex-r-sub">'+esc(e.slug)+'</span></span><span class="ex-r-kind">'+kindLabel[e.kind]+'</span></a>'
  })}
  if(res.cols.length){html+='<div class="ex-drop-label">Columns</div>';res.cols.forEach(function(c){
   var e=byId[c.id];results.push({id:c.id,href:e?e.href:'#'});
   html+='<a class="ex-result" href="'+(e?e.href:'#')+'" data-result-id="'+esc(c.id)+'"><span class="ex-glyph">'+glyphs[c.kind]+'</span><span class="ex-r-name">'+mark(c.column,res.q)+'<span class="ex-r-sub">in '+esc(c.entity)+'</span></span><span class="ex-r-kind">column</span></a>'
  })}
  if(!res.ents.length&&!res.cols.length)html='<div class="ex-empty-drop">No entity or column matches “'+esc(res.q)+'”.</div>';
  drop.innerHTML=html;drop.hidden=false;active=-1;syncActive()
 }
 function syncActive(){var els=drop.querySelectorAll('.ex-result');els.forEach(function(el,i){el.setAttribute('aria-selected',i===active?'true':'false');if(i===active){el.scrollIntoView({block:'nearest'});showPopFor(el)}})}
 function fmtStat(e){var bits=[];if(e.rows!=null)bits.push(Number(e.rows).toLocaleString('en-US')+' rows');if(e.size)bits.push(e.size);if(e.columnCount)bits.push(e.columnCount+' cols');return bits}
 function popHtml(e){
  var cols=(e.columns||[]).map(function(c){return '<span class="ex-chip">'+esc(c)+'</span>'}).join('');
  var stats=fmtStat(e).map(function(s){var m=s.match(/^([0-9.,]+)\\s+(.*)$/);return m?'<span><strong>'+esc(m[1])+'</strong> '+esc(m[2])+'</span>':'<span>'+esc(s)+'</span>'}).join('');
  return '<div class="ex-pop-head"><span class="ex-pop-glyph">'+glyphs[e.kind]+'</span><span class="ex-pop-title">'+esc(e.display)+'</span><span class="ex-pop-kind">'+kindLabel[e.kind]+'</span></div>'+
   '<p class="ex-pop-loc">'+esc(e.location)+'</p>'+
   (e.description?'<p class="ex-pop-desc">'+esc(e.description)+'</p>':'')+
   (stats?'<div class="ex-pop-stats">'+stats+(e.last?'<span>'+esc(e.last)+'</span>':'')+'</div>':(e.last?'<div class="ex-pop-stats"><span>'+esc(e.last)+'</span></div>':''))+
   (cols?'<div class="ex-pop-cols">'+cols+'</div>':'')+
   '<div class="ex-pop-open">Open '+esc(e.display)+' →</div>'
 }
 var hideTimer;
 function showPopFor(el){
  var id=el.getAttribute('data-entity-id')||el.getAttribute('data-result-id');var e=byId[id];if(!e){hidePop();return}
  clearTimeout(hideTimer);pop.innerHTML=popHtml(e);pop.hidden=false;
  var r=el.getBoundingClientRect();var pw=340,ph=pop.offsetHeight||220;
  var left=r.right+12;if(left+pw>window.innerWidth-8)left=Math.max(8,r.left-pw-12);
  if(left+pw>window.innerWidth-8)left=window.innerWidth-pw-8;
  var top=r.top;if(top+ph>window.innerHeight-8)top=Math.max(8,window.innerHeight-ph-8);
  pop.style.left=left+'px';pop.style.top=top+'px'
 }
 function hidePop(){hideTimer=setTimeout(function(){pop.hidden=true},120)}
 // hover previews on tree rows and dropdown results
 root.addEventListener('mouseover',function(ev){var el=ev.target.closest('[data-entity-id],[data-result-id]');if(el)showPopFor(el)});
 root.addEventListener('mouseout',function(ev){if(ev.target.closest('[data-entity-id],[data-result-id]'))hidePop()});
 root.addEventListener('focusin',function(ev){var el=ev.target.closest('[data-entity-id],[data-result-id]');if(el)showPopFor(el)});
 root.addEventListener('focusout',hidePop);
 if(pop){pop.addEventListener('mouseenter',function(){clearTimeout(hideTimer)});}
 if(input){
  input.addEventListener('input',function(){render(search(input.value))});
  input.addEventListener('focus',function(){if(input.value.trim())render(search(input.value))});
  input.addEventListener('keydown',function(ev){
   if(ev.key==='ArrowDown'){ev.preventDefault();if(results.length){active=Math.min(results.length-1,active+1);syncActive()}}
   else if(ev.key==='ArrowUp'){ev.preventDefault();if(results.length){active=Math.max(0,active-1);syncActive()}}
   else if(ev.key==='Enter'){if(active>=0&&results[active]){ev.preventDefault();location.href=results[active].href}}
   else if(ev.key==='Escape'){drop.hidden=true;hidePop()}
  });
  document.addEventListener('click',function(ev){if(!ev.target.closest('.ex-search')){drop.hidden=true}});
 }
 // column filter on the entity schema table
 var colFilter=root.querySelector('[data-ex-col-filter]');
 if(colFilter){colFilter.addEventListener('input',function(){
  var q=colFilter.value.trim().toLowerCase();var shown=0;
  root.querySelectorAll('[data-col-row]').forEach(function(tr){var hit=tr.getAttribute('data-col-name').indexOf(q)>=0;tr.hidden=!!q&&!hit;if(!tr.hidden)shown++});
  var count=root.querySelector('[data-col-count]');if(count)count.textContent=q?shown+' of '+root.querySelectorAll('[data-col-row]').length+' columns':root.querySelectorAll('[data-col-row]').length+' columns'
 })}
})();
`;
