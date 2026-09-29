import { z } from "zod";
import { createRouterClient } from "@orpc/server";
import { referenceSourceState } from "@mdp/contracts";
import { CopyChip, EmptyState, EntityName, relTime } from "./primitives.js";
import { Layout, Result } from "./pages.js";
import { router, type Context } from "./router.js";

// design Reference page: mirror generation, warehouse landing, counts, import progress, disk, alerts.
type Source = z.infer<typeof referenceSourceState>;
const displayNames: Record<string, string> = { musicbrainz: "MusicBrainz" };
const DISK_ALERT_PCT = 80;
export const REFRESH_COMMAND = "python3 ops/fly/mb-import/refresh.py --if-requested";
const humanize = (value: string) => { const text = value.replaceAll("_", " "); return text.charAt(0).toUpperCase() + text.slice(1); };
const displayName = (source: string) => displayNames[source] ?? humanize(source);
const utc = (at: string) => `${at.slice(0, 16).replace("T", " ")} UTC`;
function At({ at }: { at: string | null }) {
  return at ? <time datetime={at} title={relTime(at)}>{utc(at)}</time> : <span class="muted">Unknown</span>;
}
const count = (value: number) => value.toLocaleString("en-US");
function bytes(value: number) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = value, unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit++; }
  return `${unit ? size.toFixed(1) : size} ${units[unit]}`;
}
function Generation({ id }: { id: string | null }) {
  return id ? <CopyChip value={id} /> : <span class="muted">none</span>;
}
const stateTone: Record<string, string> = { running: "running", validated: "succeeded", promoted: "succeeded", failed: "failed" };

function Landing({ s }: { s: Source }) {
  const lag = !s.imported_generation ? "No validated generation in the mirror. Request a re-import below."
    : !s.landed_generation ? `Nothing has landed in the warehouse yet; the mirror import finished ${s.imported_at ? relTime(s.imported_at) : "at an unrecorded time"}.`
    : s.landed_generation !== s.imported_generation
      ? `Landed generation ${s.landed_generation} is behind the mirror (${s.imported_generation}); the mirror import finished ${s.imported_at ? relTime(s.imported_at) : "at an unrecorded time"}.`
      : "Landed generation matches the mirror.";
  const behind = Boolean(s.imported_generation) && s.landed_generation !== s.imported_generation;
  return <div class="ref-block">
    <h3>Warehouse landing</h3>
    <p class="ref-facts"><span>Generation <Generation id={s.landed_generation} /></span><span>Landed <At at={s.landed_at} /></span>
      <span>{s.landed_reconciled === null ? <span class="badge">not checked</span> : <span class={`badge ${s.landed_reconciled ? "succeeded" : "failed"}`}>{s.landed_reconciled ? "reconciled" : "does not reconcile"}</span>}</span></p>
    <p class={behind ? "ref-lag warning-text" : "ref-lag muted"} data-lag>{lag}</p>
  </div>;
}
function Counts({ s }: { s: Source }) {
  const mirror = s.mirror_counts ?? {}, landed = s.landed_counts ?? {};
  const tables = [...new Set([...Object.keys(mirror), ...Object.keys(landed)])].sort();
  if (!tables.length) return <div class="ref-block"><h3>Row counts</h3><p class="muted">No row counts yet. Run the reference probe.</p></div>;
  return <div class="ref-block">
    <h3>Row counts</h3>
    <div class="table-wrap"><table class="ref-counts"><thead><tr><th>Table</th><th>Mirror</th><th>Landed</th><th>Difference</th></tr></thead>
      <tbody>{tables.map((table) => {
        const m = mirror[table], l = landed[table];
        const diff = m === undefined || l === undefined ? undefined : l - m;
        return <tr><td><code>{table}</code></td><td>{m === undefined ? "unknown" : count(m)}</td><td>{l === undefined ? "not landed" : count(l)}</td>
          <td class={diff ? "warning-text" : "muted"}>{diff === undefined ? "unknown" : diff === 0 ? "0" : `${diff > 0 ? "+" : "−"}${count(Math.abs(diff))}`}</td></tr>;
      })}</tbody></table></div>
  </div>;
}
function Import({ s }: { s: Source }) {
  return <div class="ref-block">
    <h3>Import progress</h3>
    {s.import_state === null && !s.import_generation ? <p class="muted">No import recorded. Request a re-import below.</p> : <>
      <p class="ref-facts"><span class={`badge ${stateTone[s.import_state ?? ""] ?? ""}`}>{s.import_state ?? "unknown"}</span>
        <span>Generation <Generation id={s.import_generation} /></span><span>Phase {s.import_phase ?? "unknown"}</span></p>
      <p class="ref-facts"><span>Started <At at={s.import_started_at} /></span><span>Finished <At at={s.import_finished_at} /></span></p>
      {s.import_message ? <p class={s.import_state === "failed" ? "warning-text" : "muted"}>{s.import_message}</p> : null}
    </>}
  </div>;
}
function Disk({ s }: { s: Source }) {
  if (s.disk_used_bytes === null || s.disk_total_bytes === null || Number(s.disk_total_bytes) <= 0)
    return <div class="ref-block"><h3>Mirror disk</h3><p class="muted">Disk use unknown. Run the reference probe.</p></div>;
  const used = Number(s.disk_used_bytes), total = Number(s.disk_total_bytes);
  const pct = (used / total) * 100;
  const high = pct >= DISK_ALERT_PCT;
  return <div class="ref-block">
    <h3>Mirror disk</h3>
    <p class="ref-facts"><span>{bytes(used)} / {bytes(total)}</span><strong class={high ? "warning-text" : undefined}>{pct.toFixed(1)}%</strong>
      {high ? <span class="badge warning">at or above {DISK_ALERT_PCT}%</span> : null}</p>
    <div class="ref-disk" role="img" aria-label={`${pct.toFixed(1)}% used; alert threshold ${DISK_ALERT_PCT}%`}>
      <span class={`ref-disk-fill ${high ? "high" : ""}`} style={`width:${Math.min(100, pct).toFixed(1)}%`} />
      <span class="ref-disk-mark" style={`left:${DISK_ALERT_PCT}%`} title={`Alert threshold ${DISK_ALERT_PCT}%`} />
    </div>
    <p class="muted compact">Alert threshold {DISK_ALERT_PCT}%: imports pause at or above it.</p>
  </div>;
}
// The closure trigger: past either line the narrowed release step ships before the next import.
const NARROW_SHARE_PCT = 30;
const NARROW_RECORDINGS = 100_000;
function Closure({ s }: { s: Source }) {
  if (s.closure_write_share === null && s.closure_recordings === null)
    return <div class="ref-block"><h3>Spine closure</h3><p class="muted">Spine closure not measured. Run mb_spine to measure it.</p></div>;
  const share = s.closure_write_share === null ? null : s.closure_write_share * 100;
  const recordings = s.closure_recordings === null ? null : Number(s.closure_recordings);
  const due = (share !== null && share > NARROW_SHARE_PCT) || (recordings !== null && recordings > NARROW_RECORDINGS);
  return <div class="ref-block" data-closure>
    <h3>Spine closure</h3>
    <p class="ref-facts"><span>Projected write <strong class={share !== null && share > NARROW_SHARE_PCT ? "warning-text" : undefined}>{share === null ? "unknown" : `${share.toFixed(1)}%`}</strong> of the pgdata volume (line {NARROW_SHARE_PCT}%)</span>
      <span>Tracked recordings <strong class={recordings !== null && recordings > NARROW_RECORDINGS ? "warning-text" : undefined}>{recordings === null ? "unknown" : count(recordings)}</strong> (line {count(NARROW_RECORDINGS)})</span>
      <span>Measured <At at={s.closure_measured_at} /></span></p>
    <p class={due ? "warning-text compact" : "muted compact"}>{due ? "Past a line: ship the narrowed release step before the next import." : "Both below their lines: the closure follows every release of a tracked recording."}</p>
  </div>;
}
function Alerts({ s }: { s: Source }) {
  return <div class="ref-block">
    <h3>Open alerts</h3>
    {s.alerts.length ? s.alerts.map((a) => <div class="alert-detail ref-alert">
      <div class="row"><strong>{humanize(a.class)}</strong><span class={`badge ${a.severity}`}>{a.severity}</span></div>
      <p class="ref-facts muted"><span>Opened <At at={a.opened_at} /></span>{a.acknowledged_by ? <span>Acknowledged by {a.acknowledged_by}</span> : null}<CopyChip value={a.id} label={a.id.slice(0, 8)} />
        <a href={`/runbooks/${a.runbook_slug ?? a.class.replaceAll("_", "-")}`}>Runbook →</a></p>
    </div>) : <p class="muted">No open reference alerts.</p>}
  </div>;
}
function Reimport({ s }: { s: Source }) {
  const name = displayName(s.source);
  return <div class="ref-block">
    <h3>Re-import</h3>
    <form method="post" action="/actions/reimport" data-confirm={`Re-import ${name}? Only a validated generation replaces the current one.`}>
      <input type="hidden" name="back" value="/reference" /><input type="hidden" name="source" value={s.source} />
      <button>Re-import</button>
    </form>
    <p class="muted compact">Run <code>{REFRESH_COMMAND}</code> to process the request. It imports on a temporary volume and promotes only a validated generation.</p>
    {s.reimport_requested_at ? <p class="compact">Last requested <At at={s.reimport_requested_at} /> by {s.reimport_requested_by ?? "unknown"}</p> : null}
  </div>;
}
export function ReferenceSourceCard({ source: s }: { source: Source }) {
  return <section class="card ref-source" id={`source-${s.source}`}>
    <div class="row"><EntityName slug={s.source} displayName={displayName(s.source)} as="h2" />
      {s.alerts.length ? <span class="badge warning">{s.alerts.length} open {s.alerts.length === 1 ? "alert" : "alerts"}</span> : null}</div>
    <div class="ref-block">
      <h3>Mirror</h3>
      <p class="ref-facts"><span>Imported generation <Generation id={s.imported_generation} /></span><span>Export date <At at={s.export_date} /></span>
        <span>Imported <At at={s.imported_at} /></span>{s.replication_sequence ? <span>Replication sequence {s.replication_sequence}</span> : null}</p>
    </div>
    <Landing s={s} />
    <Counts s={s} />
    <Import s={s} />
    <Disk s={s} />
    <Closure s={s} />
    <div class="ref-block"><h3>Probe</h3>{s.probed_at ? <p>Last probed <At at={s.probed_at} /> · {relTime(s.probed_at)}</p> : <p class="muted">No probe yet. Run POST /v1/reference/probe on the functions service.</p>}
      {s.probe_error ? <p class="warning-text">Probe error: {s.probe_error}</p> : null}</div>
    <Alerts s={s} />
    <Reimport s={s} />
  </section>;
}
export const referenceCss = `.ref-source .row{align-items:flex-start}.ref-block{padding:14px 0;border-top:1px solid var(--line)}.ref-block h3{margin:0 0 8px}.ref-facts{display:flex;flex-wrap:wrap;align-items:center;gap:6px 18px;margin:4px 0}.ref-facts>span{display:inline-flex;align-items:center;gap:6px}.warning-text{color:var(--warn)}.ref-counts{max-width:640px}.ref-counts td{font-variant-numeric:tabular-nums}.ref-disk{position:relative;height:8px;max-width:420px;border-radius:4px;background:var(--paper);border:1px solid var(--line);margin:8px 0}.ref-disk-fill{position:absolute;inset:0 auto 0 0;background:var(--teal);border-radius:4px}.ref-disk-fill.high{background:var(--warn)}.ref-disk-mark{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--danger)}.ref-alert{padding:8px 0}`;
export function ReferenceView({ sources, result }: { sources: Source[]; result?: unknown }) {
  return Layout({
    title: "Reference",
    subtitle: "",
    children: <>
      <style dangerouslySetInnerHTML={{ __html: referenceCss }} />
      <Result result={result} />
      {sources.length ? sources.map((source) => <ReferenceSourceCard source={source} />) : <EmptyState message="No reference sources are registered." nextStep={{label:"Open setup guide",href:"/runbooks/reference-import-failed"}}/>}
    </>,
  });
}
export async function referencePage(context: Context, result?: unknown) {
  const sources = await createRouterClient(router, { context }).reference.list({});
  return ReferenceView({ sources, result });
}
