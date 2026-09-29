import { groupFailures } from "./console-data.js";
import type { Child } from "hono/jsx";
import { bareModel } from "./workbench-visual.js";
// primitives.tsx — foundational, imported by EntityName + RunRef
export function CopyChip({value,label,title}:{value:string;label?:string;title?:string}){
  return (
    <button type="button" class="copy-chip" data-copy-chip={value}
            title={title??`Copy ${value}`} aria-label={`Copy ${value}`}>
      <span class="copy-chip-text">{label??value}</span>
      <svg class="cc-i cc-copy" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><rect x="5" y="5" width="8" height="9" rx="1.5"/><path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2H4a1.5 1.5 0 0 0-1.5 1.5V11"/></svg>
      <svg class="cc-i cc-check" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 8.5 6.5 12 13 4"/></svg>
    </button>
  );
}


// Splits a qualified name on its LAST dot: prefix (incl. trailing dot) recedes, tail stays primary.
function QualName({name}:{name:string}){
  const bare=bareModel(name);            // strips dbt model./source./marts. first
  const dot=bare.lastIndexOf('.');
  if(dot<0) return <>{bare}</>;
  return <><span class="qual-prefix">{bare.slice(0,dot+1)}</span>{bare.slice(dot+1)}</>;
}

export function EntityName({slug,displayName,href,as='span'}:{
  slug:string; displayName?:string|null; href?:string; as?:'h2'|'strong'|'span';
}){
  const Title=as;
  const titleText = displayName
    ? <>{displayName}</>
    : <QualName name={slug}/>;
  return (
    <span class="entity">
      <Title class="entity-title">{href?<a href={href}>{titleText}</a>:titleText}</Title>
      {displayName
        ? <CopyChip value={slug} label={slug}/>              /* slug demoted to subtitle */
        : <CopyChip value={slug} title={`Copy ${slug}`} label="copy"/> /* slug already the title */}
    </span>
  );
}
export const relTime = (at:string) => {
  const m=Math.floor((Date.now()-Date.parse(at))/60000);
  if(m<0)return `in ${Math.max(1,-m)}m`;
  return m<1?'just now':m<60?`${m}m ago`:m<1440?`${Math.floor(m/60)}h ago`:`${Math.floor(m/1440)}d ago`;
};
export function RelativeTime({at}:{at:string}) { return <time data-relative datetime={at} title={at}>{relTime(at)}</time>; }
export function RunRef({id,at,kind='run',href,copy=false}:{id:string;at?:string|null;kind?:'run'|'trace'|'cycle'|'dump';href?:string;copy?:boolean}) {
  const link=href ?? (kind==='run'||kind==='trace' ? `/${kind}s/${encodeURIComponent(id)}` : undefined);
  const lead=<>{at ? <><RelativeTime at={at}/> on </> : null}<span class="ref-kind">{kind}</span>{' '}</>;
  return <span class="ref">{link ? <a class="ref-id" href={link} title={id}>{lead}{id.slice(0,8)}<span class="ref-arrow" aria-hidden="true">↗</span></a> : <>{lead}<CopyChip value={id} label={id.slice(0,8)}/></>}{link && copy ? <CopyChip value={id} label="copy"/> : null}</span>;
}

const LOC_LABEL:Record<string,string>={postgres:'Postgres · warehouse',warehouse:'Warehouse',r2:'Cloudflare R2',control:'control schema'};
function FormatGlyph({format}:{format?:string|undefined}){
  if(!format) return null;
  if(format==='source'||format==='table'||format==='postgres') // db cylinder (reused from workbench)
    return <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><ellipse cx="8" cy="3" rx="5" ry="2"/><path d="M3 3v9c0 1.1 2.2 2 5 2s5-.9 5-2V3M3 7.5c0 1.1 2.2 2 5 2s5-.9 5-2"/></svg>;
  if(format==='r2'||format==='file') // object/box glyph
    return <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M8 2 14 5v6l-6 3-6-3V5z"/><path d="M2 5l6 3 6-3M8 8v6"/></svg>;
  return <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><rect x="2" y="2" width="9" height="8" rx="1.5"/><path d="M5 12h7a2 2 0 0 0 2-2V5M5 5h3M5 7h3"/></svg>; // model/view
}
export type Facts={location?:string;format?:string;rows?:number|string|null;size?:string|null;columns?:number|null;shape?:{name:string;type:string}[];description?:string|null;details?:{label:string;value:string|number|null|undefined}[];links:[{label:string;href:string}, ...{label:string;href:string}[]]};
export function EntityPopover({label,facts,summary}:{label:Child;facts:Facts;summary:string}){
  const stats=[facts.rows!=null?`${facts.rows} rows`:null,facts.columns!=null?`${facts.columns} cols`:null,facts.size||null].filter(Boolean);
  return (
    <span class="pop" tabindex={0} title={summary}>
      <span class="pop-trigger">{label}</span>
      <span class="pop-body pop-panel" role="tooltip">
        <span class="pop-head"><FormatGlyph format={facts.format}/><strong>{summary}</strong>{facts.location?<span class="pop-loc">{LOC_LABEL[facts.location]??facts.location}</span>:null}</span>
        {facts.details?.filter(f=>f.value!=null).map(f=><span class="pop-fact"><span class="muted">{f.label}</span><span>{f.value}</span></span>)}
        {stats.length?<span class="pop-stats">{stats.map(s=><span>{s}</span>)}</span>:null}
        {facts.description?<span class="pop-desc">{facts.description}</span>:null}
        {facts.shape?.length?<span class="pop-shape">{facts.shape.slice(0,6).map(c=><span class="pop-col"><span class="pc-name">{c.name}</span><span class="pc-type">{c.type}</span></span>)}{facts.shape.length>6?<span class="pop-more">+{facts.shape.length-6} more</span>:null}</span>:null}
        {facts.links?.length?<span class="pop-links">{facts.links.map(l=><a href={l.href}>{l.label} ↗</a>)}</span>:null}
      </span>
    </span>
  );
}
// columns sorted so system (_prefixed) columns fall last, matching existing function-page behavior
export function SchemaDisclosure({columns,label='columns',open=false}:{
  columns:{name:string;type:string;nullable:boolean;description?:string|null}[]; label?:string; open?:boolean;
}){
  const cols=[...columns].sort((a,b)=>Number(a.name.startsWith('_'))-Number(b.name.startsWith('_')));
  const anyDesc=cols.some(c=>c.description);
  return (
    <details class="schema" open={open}>
      <summary class="schema-summary"><span class="schema-count">Output schema · {cols.length} {label}</span>
        <span class="schema-peek">{cols.slice(0,4).map(c=>c.name).join(' · ')}{cols.length>4?' …':''}</span></summary>
      <div class="schema-body"><table class="schema-table"><thead><tr><th>column</th><th>type</th><th>null</th>{anyDesc?<th>description</th>:null}</tr></thead>
        <tbody>{cols.map(c=><tr><td class="sc-name">{c.name}</td><td class="sc-type">{c.type}</td>
          <td>{c.nullable?<span class="sc-null">nullable</span>:<span class="sc-req">required</span>}</td>
          {anyDesc?<td class="sc-desc">{c.description??<span class="muted">—</span>}</td>:null}</tr>)}</tbody></table></div>
    </details>
  );
}
export const primitivesCss = `.failure-banner{border-left:4px solid var(--danger);background:color-mix(in srgb,var(--danger) 5%,var(--surface))}.failure-item+.failure-item{border-top:1px solid var(--line);margin-top:12px;padding-top:12px}.failure-item p{margin:4px 0;overflow-wrap:anywhere}.semantic-pop{position:relative;display:inline-block;max-width:100%}.semantic-pop>summary{list-style:none;text-decoration:underline dotted;text-underline-offset:3px;font-size:12px}.semantic-panel{position:absolute;z-index:40;left:0;top:calc(100% + 6px);width:300px;max-width:calc(100vw - 24px);max-height:70vh;overflow:auto;white-space:normal;background:var(--surface);padding:14px;border:1px solid var(--line);border-radius:var(--card-radius);box-shadow:0 12px 40px #182b3520;font-size:12px}.semantic-panel p{margin:0 0 10px}.semantic-panel li{margin:8px 0;overflow-wrap:anywhere}.semantic-panel ul{padding-left:18px}.coverage-meter{display:inline-flex;gap:7px;align-items:center;flex-wrap:wrap}.coverage-meter meter{width:64px;height:8px;accent-color:var(--teal)}.label-chips{display:inline-flex;gap:4px;flex-wrap:wrap;white-space:normal}.label-chips .badge{white-space:normal;font-size:10px}.cadence-pills{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.cadence-pill{padding:8px 10px;border:1px solid var(--line);border-radius:var(--radius);font-size:12px}.changed-dot{color:var(--teal);font-size:11px;margin-left:6px}.event-label{display:table;margin-top:4px}kbd{font:10px ui-monospace,monospace;opacity:.75}.result[role=status]{border-left:3px solid var(--teal)}@media(max-width:650px){.semantic-panel{position:static;margin-top:8px;width:100%}.semantic-pop[open]{display:block;min-width:0;width:100%}.cadence-pill{width:100%}}
.copy-chip{display:inline-flex;align-items:center;gap:5px;padding:2px 6px;font:11px/1 ui-monospace,monospace;background:var(--paper);color:var(--muted);border:1px solid var(--line);border-radius:var(--radius);cursor:pointer;max-width:100%;transition:color .12s ease-out,border-color .12s ease-out,background-color .12s ease-out}
.copy-chip-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.copy-chip:hover{color:var(--teal);border-color:var(--teal);background:color-mix(in srgb,var(--teal) 6%,var(--surface))}
.copy-chip .cc-i{flex:0 0 11px;opacity:.65}
.copy-chip:hover .cc-i{opacity:1}
.copy-chip .cc-check{display:none}
.copy-chip.copied{color:var(--teal);border-color:var(--teal);background:color-mix(in srgb,var(--teal) 10%,var(--surface))}
.copy-chip.copied .cc-copy{display:none}
.copy-chip.copied .cc-check{display:inline;animation:cc-pop .34s ease-out}
@keyframes cc-pop{0%{transform:scale(.4);opacity:0}55%{transform:scale(1.18)}100%{transform:scale(1);opacity:1}}
@media(prefers-reduced-motion:reduce){.copy-chip.copied .cc-check{animation:none}}
.entity{display:inline-flex;flex-direction:column;align-items:flex-start;gap:3px;min-width:0;vertical-align:top}
.entity-title{font-size:inherit;font-weight:600;letter-spacing:-.2px;color:var(--foreground);line-height:1.2;margin:0;overflow-wrap:anywhere}
.entity-title a{color:inherit}
.entity-title a:hover{color:var(--teal)}
/* middle weight (500) + mid color between --surface and --muted, so the tail is what the eye lands on */
.qual-prefix{font-weight:500;color:color-mix(in srgb,var(--foreground) 55%,var(--muted))}
.entity .copy-chip{font-size:10px;padding:1px 5px}
.ref{display:inline-flex;align-items:baseline;gap:5px;flex-wrap:wrap;min-width:0;font-size:12px;color:var(--muted)}
.ref time{color:var(--muted)}
.ref-kind{color:var(--muted)}
.ref-id{font-family:ui-monospace,monospace;color:var(--teal);text-decoration:underline;text-decoration-color:color-mix(in srgb,var(--teal) 40%,transparent);text-underline-offset:2px}
.ref-id:hover{text-decoration-color:var(--teal)}
.ref-arrow{margin-left:2px;font-size:10px;opacity:.6}
.pop{position:relative;display:inline-flex;cursor:default;min-width:0}
.pop-trigger{text-decoration:underline dotted;text-decoration-color:var(--line);text-underline-offset:3px;overflow-wrap:anywhere}
.pop:hover .pop-trigger,.pop:focus-within .pop-trigger{text-decoration-color:var(--teal)}
.pop-body{position:absolute;z-index:25;left:0;top:calc(100% + 6px);width:max-content;max-width:300px;display:flex;flex-direction:column;gap:8px;background:var(--surface);border:1px solid var(--line);border-radius:var(--card-radius);padding:12px 14px;box-shadow:0 12px 44px color-mix(in srgb,var(--foreground) 12%,transparent);opacity:0;visibility:hidden;transform:translateY(-4px);transition:opacity .14s ease-out,transform .14s ease-out;pointer-events:none}
.pop:hover .pop-body,.pop:focus-within .pop-body{opacity:1;visibility:visible;transform:none;pointer-events:auto}
.pop-head{display:flex;align-items:center;gap:6px;font-size:13px}.pop-head strong{color:var(--foreground);overflow-wrap:anywhere}.pop-head svg{flex:0 0 12px;opacity:.6}
.pop-loc{margin-left:auto;font-size:11px;color:var(--muted)}
.pop-stats{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.pop-desc{font-size:12px;color:var(--foreground);line-height:1.5;padding-top:8px;border-top:1px solid var(--line)}
.pop-shape{display:flex;flex-direction:column;gap:3px;padding-top:8px;border-top:1px solid var(--line)}
.pop-col{display:flex;justify-content:space-between;gap:12px;font:11px/1.4 ui-monospace,monospace}.pc-name{color:var(--foreground)}.pc-type{color:var(--muted)}
.pop-more{font-size:11px;color:var(--muted)}
.pop-links{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;padding-top:8px;border-top:1px solid var(--line)}
@media(max-width:650px){.pop-body{left:auto;right:0}}
.schema{margin:10px 0}
.schema>summary{font-size:12px;color:var(--teal);font-weight:600;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.schema-count{color:var(--foreground)}
.schema-peek{color:var(--muted);font:11px/1.4 ui-monospace,monospace;font-weight:400;overflow:hidden;text-overflow:ellipsis}
.schema-body{margin-top:10px;max-height:320px;overflow:auto;border:1px solid var(--line);border-radius:var(--radius)}
.schema-table{width:100%;border-collapse:collapse;font-size:12px}
.schema-table th{position:sticky;top:0}
.sc-name{font-family:ui-monospace,monospace;color:var(--foreground)}
.sc-type{font-family:ui-monospace,monospace;color:var(--muted)}
.sc-desc{white-space:normal;max-width:280px;color:var(--foreground)}
.sc-null{font-size:10px;color:var(--warn)}
.sc-req{font-size:10px;color:var(--muted)}
.pop-body{white-space:normal}.pop-body::before{content:"";position:absolute;left:0;right:0;top:-8px;height:8px}.pop-fact{display:flex;justify-content:space-between;gap:12px;font-size:12px}.output-chips{display:flex;flex-direction:column;align-items:flex-start;gap:8px}.output-chips>.pop{padding:6px 8px;border:1px solid var(--line);border-radius:var(--radius)}.platform-glyph{display:inline-flex;width:18px;height:18px;align-items:center;justify-content:center;color:var(--muted);flex-shrink:0}.receipt-facts{display:flex;gap:8px 16px;align-items:center;flex-wrap:wrap;margin:10px 0;font-size:12px}.entity-title{white-space:normal}.ref-id{font-family:inherit}.ref-id time{color:inherit}.pop:focus-visible{outline:2px solid var(--teal);outline-offset:3px}@media(prefers-reduced-motion:reduce){.pop-body,.copy-chip{transition:none}}
`;

import { healthState, refusalInfo, type Coverage, type Health, type Failure, type Labels, type Drift } from './console-semantics.js';
export type NextStep = {label:string; href:string} | {label:string; command:string};
export function NextAction({step}:{step:NextStep}) {
  return 'href' in step ? <a href={step.href}>{step.label} →</a> : <CopyChip value={step.command} label={step.label}/>;
}
export function EmptyState({message,nextStep}:{message:string;nextStep:NextStep}) {
  return <p class="muted">{message} <NextAction step={nextStep}/></p>;
}
export function Notice({children,nextStep,tone='result'}:{children:Child;nextStep:NextStep;tone?:'error'|'result'|'warning'}) {
  return <section class={`card ${tone}`} role={tone==='error'?'alert':'status'}>{children}<div class="actions"><NextAction step={nextStep}/></div></section>;
}
// Native disclosure keeps the panel usable by keyboard, touch and without JavaScript.
export function DetailPopover({label,children,nextStep}:{label:Child;children:Child;nextStep:NextStep}) {
  return <details class="semantic-pop"><summary>{label}</summary><div class="semantic-panel">{children}<div class="actions"><NextAction step={nextStep}/></div></div></details>;
}
export function FailureBanner({
  failures,
  all = false,
}: {
  failures: Failure[];
  all?: boolean;
}) {
  const groups = groupFailures(failures);
  if (!groups.length) return null;
  const visible = all ? groups : groups.slice(0, 3);
  const retry = (f: Failure) =>
    f.cycle_id && f.retryable !== false ? (
      <form
        method="post"
        action="/actions/retry"
        data-confirm="Retry this cycle?"
      >
        <input type="hidden" name="cycle_id" value={f.cycle_id} />
        <button>
          Retry cycle <kbd>↵</kbd>
        </button>
      </form>
    ) : null;
  return (
    <section
      class="card failure-banner"
      aria-label="Platform needs attention"
      role="alert"
    >
      {visible.map((g) => {
        const ordered = [...g.failures].sort((a, b) =>
          (b.at ?? "").localeCompare(a.at ?? ""),
        );
        const latest = ordered[0]!;
        return (
          <div class="failure-group">
            <h2>
              {g.title}
              {g.count > 1 ? ` × ${g.count}` : ""}
            </h2>
            <p>
              {g.summary} {g.next_step}
            </p>
            {latest.note && (
              <p class="muted">
                {latest.note} <a href={g.runbook}>Recovery guide →</a>
              </p>
            )}
            <div class="actions">
              {retry(latest)}
              {latest.run_id && <RunRef id={latest.run_id} />}
              <a
                href={
                  g.source_key === "platform"
                    ? "/ops#alerts"
                    : `/functions/${g.source_key}`
                }
              >
                Open function →
              </a>
              <a href={g.runbook}>Recovery guide →</a>
            </div>
            <details>
              <summary>Details</summary>
              {[
                ...new Set(ordered.flatMap((f) => (f.model ? [f.model] : []))),
              ].map((model) => (
                <p>
                  <code>{model}</code>
                </p>
              ))}
              {g.cycle_ids.map((id) => (
                <div class="actions">
                  <CopyChip value={id} label={`Cycle ${id.slice(0, 8)}`} />
                  {retry(ordered.find((f) => f.cycle_id === id)!)}
                </div>
              ))}
              <pre class="failure-message">
                {ordered.map((f) => f.message).join("\n\n")}
              </pre>
              <CopyChip
                value={ordered.map((f) => f.message).join("\n\n")}
                label="Copy error"
              />
            </details>
          </div>
        );
      })}
      {groups.length > visible.length && (
        <a href="/ops#alerts">{groups.length - visible.length} more on Ops →</a>
      )}
    </section>
  );
}
export function CoverageMeter({coverage}:{coverage?:Coverage|undefined}) {
  if(!coverage)return <span class="muted">Target coverage not recorded · <a href="/runbooks/partial-coverage">Coverage guide →</a></span>;
  const c=coverage;
  return <DetailPopover nextStep={{label:"Coverage guide",href:"/runbooks/partial-coverage"}} label={<span class="coverage-meter"><meter min="0" max={Math.max(1,c.total)} value={c.succeeded} aria-label={`${c.succeeded} of ${c.total} targets succeeded`}/><span>{c.succeeded} / {c.total} targets succeeded{c.floor!=null?` · floor ${Math.round(c.floor*100)}%`:''}</span></span>}><p>Successful targets from this run’s frozen target list.</p>{c.total===0?<p>No targets in this run.</p>:c.failures.length?<ul>{c.failures.map(f=><li><a href={`/targets/${f.id}`}>{f.name}</a> · {f.reason}</li>)}</ul>:<p>{c.succeeded===c.total?'Every target succeeded.':'Remaining targets have not finished.'}</p>}</DetailPopover>;
}
export function TargetHealth({health,back='/ops#targets'}:{health:Health;back?:string}) {
  const state=healthState(health),tone=state==='healthy'||state==='unchanged'?'active':state==='dead'?'critical':'warning';
  return <><DetailPopover nextStep={{label:"Target and audit",href:`/targets/${health.id}`}} label={<span class={`badge ${tone}`}>{state}</span>}><strong>{health.name}</strong><p>{health.result??'No request recorded.'}</p><p>{health.at?<RelativeTime at={health.at}/>:null}{health.http_status!=null?` · Last HTTP response: ${health.http_status}`:''}</p><p>{state==='dead'?'Repeated 404/410 responses across runs.':state==='parked'?'Excluded from new cycles. Select Reactivate to try it again.':state==='stale'?'The latest request failed; review it before retrying.':state==='healthy'?'The latest request succeeded.':'Run this source to record its first result.'}</p><div class="actions">{health.resolved&&<form method="post" action="/actions/activate"><input type="hidden" name="id" value={health.id}/><input type="hidden" name="active" value={String(!health.active)}/><input type="hidden" name="back" value={back}/><button class="secondary">{health.active?'Deactivate':'Reactivate'}</button></form>}{health.probe_action?.startsWith('/actions/')&&<form method="post" action={health.probe_action}><input type="hidden" name="id" value={health.id}/><input type="hidden" name="back" value={back}/><button>Probe target</button></form>}</div></DetailPopover>{health.zero_yield_run&&<DetailPopover nextStep={{label:"Inspect producing run",href:`/runs/${health.zero_yield_run}`}} label={<span class="badge warning">No output</span>}><strong>{health.name}</strong><p>This target completed after excluding or rejecting every row.</p><a href="/runbooks/target-zero-yield">Review exclusion reasons</a></DetailPopover>}</>;
}
export function DriftPopover({changes,fingerprint,source,acknowledged=false}:{changes:Drift[];fingerprint:string;source:string;acknowledged?:boolean}) {
  return <div class="actions"><DetailPopover nextStep={{label:"Schema guide",href:"/runbooks/schema-drift"}} label={`Schema changes · ${changes.length}`}><p>Compare this output with the previous schema for the same table.</p>{changes.length?<ul>{changes.map(c=><li><code>{c.name}</code> · {c.before?c.after?`${c.before} → ${c.after}`:`removed (${c.before})`:`added ${c.after}`}{c.own_code&&<span class="badge active">Declared change</span>}</li>)}</ul>:<p>No comparison is recorded yet.</p>}</DetailPopover><form method="post" action="/actions/drift"><input type="hidden" name="source_key" value={source}/><input type="hidden" name="fingerprint" value={fingerprint}/><input type="hidden" name="back" value={`/functions/${source}`}/><button class="secondary" disabled={acknowledged}>{acknowledged?'Acknowledged':'Acknowledge'}</button></form></div>;
}
// A row counts as a refusal when it carries an error class or is not an info or debug event.
// Rows without a level (runs, alerts, audit results) keep the refusal hint.
export function isRefusal(row:Record<string,unknown>) {
  return row.error_class!=null || !['info','debug'].includes(String(row.level ?? 'error'));
}
// An info event says what happened in plain words, under its message, never as a refusal.
export function EventLabel({type}:{type:string}) {
  return <span class="badge">{type.replaceAll('_',' ') || 'event'}</span>;
}
export function RefusalPopover({code,message,runId,traceId,auditId}:{code:string;message?:string|undefined;runId?:string|undefined;traceId?:string|undefined;auditId?:string|undefined}) {
  const info=refusalInfo(code,message);
  return <DetailPopover label={<span class="badge warning">Request refused · {info.label}</span>} nextStep={{label:'Open trace',href:traceId ? `/traces/${encodeURIComponent(traceId)}` : runId ? `/traces/${encodeURIComponent(runId)}` : '/runs'}}><p>{info.message}</p><p><strong>Next step:</strong> {info.path}</p>{info.runbook&&<a href={info.runbook}>Recovery guide →</a>} <a href={auditId ? `/audit/${encodeURIComponent(auditId)}` : '/audit'}>View audit →</a></DetailPopover>;
}
export function LabelChips({labels}:{labels:Labels}) {
  return <span class="label-chips">{(['layer','category','tenant'] as const).map(k=><span class="badge" title={`${k}: ${labels[k]??'unknown'}`}>{k}: {labels[k]??'unknown'}</span>)}<span class="badge" title="Rights annotate rows; learning and resale are separate permissions.">rights: {labels.per_row?'check each row · ':''}{labels.learning_eligible==null?'learning unknown':labels.learning_eligible?'learning allowed':'no learning'} · {labels.resale_permitted==null?'resale unknown':labels.resale_permitted?'resale allowed':'no resale'}{labels.rights_status?` · ${labels.rights_status}`:''}</span></span>;
}
export function CadencePill({cadence,scope,overdue,lastAge,interval,openAge,failed=false}:{failed?:boolean;cadence:string;scope:string;overdue:boolean;lastAge?:number;interval:number;openAge?:number}) {
  const remaining=lastAge==null?null:Math.max(0,interval-lastAge);
  return <div class="cadence-pill"><strong>{cadence} · {scope}</strong> <span class={`badge ${failed?'failed':overdue?'warning':lastAge==null?'pending':'active'}`}>{failed?'failed':overdue?'overdue':lastAge==null?'awaiting first close':'current'}</span><div class="muted">{remaining==null?'Next due after the first close':remaining===0?'Due now':`Next due in ${Math.ceil(remaining/60)} min`}{openAge!=null?` · open for ${Math.floor(openAge/60)} min`:''}</div></div>;
}
export function ChangedDot({at}:{at:string}) {return <span class="changed-dot" data-alert-changed={at} hidden title="Opened since your last alert visit">● New since your last visit</span>;}
