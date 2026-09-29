import type { Coverage } from "./console-semantics.js";
import { z } from "zod";
import { runDto, functionPage } from "@mdp/contracts";
type Run = z.infer<typeof runDto>;
type Receipt = z.infer<typeof functionPage>["receipts"][number][number];
import { CoverageMeter, RefusalPopover, CopyChip, RunRef } from "./primitives.js";
export { RelativeTime, relTime } from "./primitives.js";
export function RecentRuns({runs,progress=[],coverage={}}:{runs:Run[];coverage?:Record<string,Coverage>;progress?:{run_id:string;done:string;total:string}[]}) {
 const live=runs.some(r=>['queued','running','draining'].includes(r.status));
 return <section class="card" id="recent-runs" data-live-run={live ? 'true' : undefined}>
  <div class="row"><h2>Recent runs</h2>{live ? <div class="live-controls"><span class="badge running" role="status"><span class="live-dot"/>Refreshing</span><button type="button" class="secondary" data-pause-runs aria-pressed="false">Pause polling</button></div> : null}</div>
  <div class="table-wrap"><table><thead><tr><th>Run</th><th>Status / coverage</th><th>Landed / rejected</th><th>Trace</th></tr></thead><tbody>{runs.map((r,i)=><tr hidden={i>=5} data-run-id={r.id} data-landed={r.rows_written} data-status={r.status} data-rejected={r.rows_rejected}>
   <td><RunRef id={r.id} at={r.created_at}/></td>
   <td>{['queued','running','draining'].includes(r.status) && <div class="muted">{Number(progress.find(p=>p.run_id===r.id)?.total)>0 ? `${progress.find(p=>p.run_id===r.id)?.done} / ${progress.find(p=>p.run_id===r.id)?.total} targets processed` : 'Preparing targets'}</div>}<span class={`badge ${r.status}`}>{r.status}</span> <span class={`badge ${r.coverage}`}>{r.coverage ?? 'Pending'}</span><div><CoverageMeter coverage={coverage[r.id]}/></div></td>
   <td>{Number(r.rows_written)>0 ? <a href={`#receipts-${r.id}`} data-open-receipts={r.id}>{r.rows_written} landed</a> : <span class="muted">0 landed</span>} {Number(r.rows_rejected)>0 ? <a href={`#receipts-${r.id}`} data-open-receipts={r.id} class="badge warning">rejected {r.rows_rejected} ↗</a> : <span class="badge">rejected 0</span>}</td>
   <td><a href={`/traces/${r.trace_id ?? r.id}`}>Open trace →</a></td>
  </tr>)}</tbody></table></div>
 </section>;
}
export function RunReceipts({runs,receipts,expanded=false,coverage={},id="receipts",showCoverage=true}:{runs:Run[];receipts:Receipt[][];expanded?:boolean;coverage?:Record<string,Coverage>;id?:string;showCoverage?:boolean}) {
 return <section class="card" id={id}><div class="row"><h2>Receipts</h2><label>Run<select data-receipt-filter><option value="">Recent runs</option>{runs.map(r=><option value={r.id}>{r.id.slice(0,8)} · {r.status}</option>)}</select></label></div>
 {runs.map((r,i)=><details open={expanded} class={`receipt-group ${Number(r.rows_rejected)>0 ? "has-rejected" : ""}`} id={`receipts-${r.id}`} data-receipt-run={r.id}><summary><RunRef id={r.id} at={r.created_at}/> · <span class={Number(r.rows_written)>0 && !Number(r.rows_rejected) ? "receipt-count" : ""}>{r.rows_written} landed</span> · <span class={Number(r.rows_rejected)>0 ? "receipt-count" : ""}>rejected {r.rows_rejected}</span></summary>
 {showCoverage && <CoverageMeter coverage={coverage[r.id]}/>}{(r.error_class||r.status==="failed")&&<RefusalPopover code={r.error_class??"unmapped"} message={r.error_message??undefined} runId={r.id} traceId={r.trace_id??undefined}/>}<p><a href={`/runs/${r.id}`}>Open run →</a></p>
 {receipts[i]?.length ? [...receipts[i]!].sort((a,b)=>Number(b.rows_rejected)-Number(a.rows_rejected)).map((receipt,j)=><details class="receipt-detail" id={`receipt-${r.id}-${receipt.dump_id??j}`}><summary>Receipt {j+1} · <span class={Number(receipt.rows_written)>0 && !Number(receipt.rows_rejected) ? "receipt-count" : ""}>{String(receipt.rows_written)} written</span> · <span class={Number(receipt.rows_rejected)>0 ? "receipt-count" : ""}>{String(receipt.rows_rejected)} rejected</span>{Number(receipt.rows_rejected)>0 && <> · <strong>{receipt.message}</strong></>}</summary><div class="receipt-facts"><span>Row coverage: {receipt.row_coverage ?? "Not recorded"}{typeof receipt.row_rejection_share === "number" ? ` · ${Math.round(receipt.row_rejection_share * 100)}% error rejections` : ""}{typeof receipt.min_row_coverage === "number" ? ` · ${Math.round(receipt.min_row_coverage * 100)}% acceptance floor` : ""}{receipt.row_coverage_met === false ? " · Below floor" : ""}</span>{typeof receipt.targets_total === "number" ? <span>Target coverage: {receipt.targets_succeeded}/{receipt.targets_total}{typeof receipt.min_target_coverage === "number" ? ` · ${Math.round(receipt.min_target_coverage * 100)}% floor` : ""}</span> : null}{Number(receipt.rows_excluded)>0 ? <span>Expected exclusions: {receipt.rows_excluded}{Object.entries(receipt.row_exclusions ?? {}).map(([reason,count])=><span> · {reason}: {count}</span>)}</span> : null}<a href="/runbooks/partial-coverage">Check coverage</a><RunRef id={receipt.run_id} at={runs.find(run=>run.id===receipt.run_id)?.created_at ?? null}/>{receipt.dump_id ? <span>Output file <CopyChip value={receipt.dump_id} label={receipt.dump_id.slice(0,8)}/></span> : null}{receipt.landed_seq!==null ? <span>Landed sequence <strong>{receipt.landed_seq}</strong></span> : null}<a href={/^(https?:\/\/|\/(?!\/))/.test(receipt.trace_url) ? receipt.trace_url : `/traces/${runs.find(run=>run.id===receipt.run_id)?.trace_id ?? receipt.run_id}`}>Open trace ↗</a></div>
 {receipt.message ? <><p>{receipt.message}</p><RefusalPopover code={receipt.error_class??r.error_class??"unmapped"} message={receipt.message} runId={r.id} traceId={r.trace_id??undefined}/></> : null}
 {receipt.loads.length ? <details><summary>Loads · {receipt.loads.length}</summary><div class="table-wrap"><table><thead><tr><th>Output file</th><th>Status</th><th>Generation</th><th>Rows inserted</th></tr></thead><tbody>{receipt.loads.map(load=><tr><td><CopyChip value={load.dump_id} label={load.dump_id.slice(0,8)}/></td><td>{load.status}</td><td>{load.generation}</td><td>{load.rows_inserted ?? 'unknown'}</td></tr>)}</tbody></table></div></details> : null}
 <details id={`raw-receipt-${r.id}-${j}`}><summary>Raw receipt</summary><pre>{JSON.stringify(receipt,null,2)}</pre></details></details>) : <p class="muted">No receipts yet. Run the function to land rows.</p>}
 </details>)}
 </section>;
}
export function Sparkline({runs,mini=false}:{runs:Run[];mini?:boolean}) {
 const ordered=[...runs].filter(r=>!['queued','running','draining'].includes(r.status)).reverse();
 if(ordered.length<=1 || ordered.every(r=>Number(r.rows_written)===0))return <p class="spark-empty">{ordered.some(r=>Number(r.rows_written)>0)?`${ordered[0]!.rows_written} rows landed`:"No rows landed"}</p>;
 const max=Math.max(1,...ordered.map(r=>Number(r.rows_written)));
 const x=(i:number)=>24+i*452/Math.max(1,ordered.length-1);
 const y=(r:Run)=>100-Number(r.rows_written)*58/max;
 const sameDay=ordered[0]?.created_at.slice(0,10)===ordered.at(-1)?.created_at.slice(0,10);
 const label=(at:string|undefined)=>at ? sameDay ? `${at.slice(11,16)} UTC` : at.slice(0,10) : '';
 const line=ordered.map((r,i)=>r.status==='superseded' ? '' : `${i===0 || ordered[i-1]?.status==='superseded' ? 'M':'L'}${x(i)},${y(r)}`).join(' ');
 return <svg class={`spark ${mini ? 'mini-spark' : ''}`} viewBox="0 0 500 140" role="img" aria-label="Rows landed per run; grey ticks indicate superseded runs">
 <path d="M24 100H476" stroke="var(--line)"/><path d={line} fill="none" stroke="var(--teal)" stroke-width="3"/>
 {ordered.map((r,i)=>r.status==='superseded' ? <path d={`M${x(i)} 97v6`} stroke="var(--muted)" stroke-width="2"><title>Superseded</title></path> : <a class="spark-point" href={`/runs/${r.id}`} aria-label={`${r.rows_written} rows, ${r.created_at}`}>
 <rect x={x(i)-Math.min(16,226/ordered.length)} y="35" width={Math.min(32,452/ordered.length)} height="72" fill="transparent"/>
 <circle cx={x(i)} cy={y(r)} r="5" fill={r.coverage==='partial' ? 'var(--warn)' : r.status==='failed' ? 'var(--danger)' : 'var(--teal)'}/>
 <circle class="hover-ring" cx={x(i)} cy={y(r)} r="10" fill="none" stroke="var(--teal)" stroke-width="2"/>
 {i===ordered.length-1 ? <text class="last-value" x={x(i)} y={y(r)-17} text-anchor="end" font-size="12">{r.rows_written}</text> : null}
 <g class="spark-tooltip"><rect x={Math.max(8,Math.min(mini?132:272,x(i)-(mini?180:110)))} y="2" width={mini?360:220} height={mini?42:28} rx="5" fill="var(--teal-dark)"/><text x={Math.max(16,Math.min(mini?140:280,x(i)-(mini?172:102)))} y={mini?32:22} fill="white" font-size={mini?28:16}>{r.rows_written} rows · {Math.max(0,Math.floor((Date.now()-Date.parse(r.created_at))/60000))}m ago</text></g>
 </a>)}
 {!mini ? <><text x="24" y="128" font-size="11" fill="var(--muted)">{label(ordered[0]?.created_at)}</text><text x="476" y="128" text-anchor="end" font-size="11" fill="var(--muted)">{label(ordered.at(-1)?.created_at)}</text></> : null}
 </svg>;
}
