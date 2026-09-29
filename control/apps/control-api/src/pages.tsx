import { consoleFailures, targetHealth } from "./console-data.js";
import { driftHistory, type Failure } from "./console-semantics.js";
import { EmptyState, Notice, NextAction, type NextStep, TargetHealth, DriftPopover, RefusalPopover, EventLabel, isRefusal, CadencePill, ChangedDot, primitivesCss, CopyChip, EntityName, EntityPopover, RunRef, relTime } from "./primitives.js";
import { round4Css } from "./visual-round.js";
import { attention, pausedSince, budgetSpend, recoveryChoices } from "./page-data.js";
import { readPreview } from "./import-preview.js";
import { inspectTargetCsv } from "./csv.js";
import { RelativeTime } from "./run-components.js";
import { randomUUID } from "node:crypto";
import { createRouterClient } from "@orpc/server";
import { html } from "hono/html";
import { z } from "zod";
import { rows } from "./db.js";
import { emailTransport } from "./email.js";
import { type PlatformStatus, jsonRow } from "@mdp/contracts";
import { router, type Context } from "./router.js";
export const css = `:root{--surface:#fff;--foreground:#182b35;--muted:#61767c;--line:#dbe5e3;--paper:#f4f6f5;--teal:#0b7871;--teal-dark:#102d35;--danger:#b33d30;--warn:#95670a;--radius:6px;--card-radius:10px;font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:var(--foreground);background:var(--paper);font-size:15px;line-height:1.5}*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0}header{background:var(--teal-dark);color:white;padding:22px max(28px,calc((100vw - 1380px)/2));display:flex;align-items:center;justify-content:space-between}header strong{font-size:19px;letter-spacing:-.5px}header span{opacity:.65;font-size:12px;margin-left:14px}nav{display:flex;gap:24px}a{color:var(--teal);text-decoration:none}nav a{color:white;opacity:.8}a:hover{text-decoration:underline;color:var(--teal-dark)}nav a:hover{color:white;opacity:1}main{max-width:1436px;margin:auto;padding:36px 28px 72px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:2px;font-weight:700;color:var(--muted)}h1{font-size:38px;line-height:1.15;letter-spacing:-1.4px;margin:10px 0;overflow-wrap:anywhere}h1.title-mono{font-family:ui-monospace,SFMono-Regular,monospace;font-size:31px;font-weight:600;letter-spacing:-.5px}h2{font-size:18px;letter-spacing:-.3px;margin:0 0 16px}h3{font-size:14px;margin:0 0 8px}.lead{color:var(--muted);margin:8px 0 28px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(290px,100%),1fr));gap:20px}.card{background:white;border:1px solid var(--line);border-radius:var(--card-radius);padding:22px;margin:0 0 22px}.grid .card{margin:0}.metric{font-size:42px;letter-spacing:-1.4px;line-height:1.2;font-weight:650}.muted,.empty{color:var(--muted)}.empty{padding:16px;background:var(--paper);border:1px dashed var(--line);border-radius:var(--radius)}.table-wrap{overflow:auto;max-width:100%}.expanded-table{max-height:380px}table{width:100%;border-collapse:collapse;font-size:13px}th{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);white-space:nowrap;text-align:left;background:var(--paper);position:sticky;top:0}td,th{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:middle}td{max-width:340px;white-space:nowrap}tbody tr:hover{background:color-mix(in srgb,var(--teal) 5%,white)}td details{white-space:normal;min-width:120px;max-width:300px}.badge{display:inline-flex;padding:3px 8px;border-radius:var(--radius);font-size:11px;background:var(--paper);color:var(--muted);white-space:nowrap}.succeeded,.resolved,.full,.active{background:color-mix(in srgb,var(--teal) 12%,white);color:var(--teal)}.failed,.critical,.stale{background:color-mix(in srgb,var(--danger) 10%,white);color:var(--danger)}.partial,.warning,.paused,.pending{background:color-mix(in srgb,var(--warn) 12%,white);color:var(--warn)}.running,.queued{background:color-mix(in srgb,var(--teal) 10%,white);color:var(--teal)}form{display:flex;gap:9px;align-items:end;flex-wrap:wrap}form.stack{display:grid;gap:12px}label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:4px;min-width:0}input,select,textarea,button{font:inherit;border:1px solid var(--line);border-radius:var(--radius);padding:8px 10px;background:white;max-width:100%}input,select{max-width:min(230px,100%)}textarea{width:100%;min-height:95px}button,.button{background:var(--teal);color:white;border-color:var(--teal);cursor:pointer;font-size:12px;font-weight:600;white-space:nowrap}button,a{transition:background-color .12s ease-out,border-color .12s ease-out,color .12s ease-out}button:hover{background:var(--teal-dark)}button:active,.button:active{transform:translateY(1px)}button:disabled{opacity:.5;cursor:not-allowed}.secondary{background:white;color:var(--teal);border-color:var(--line)}button.secondary:hover{background:color-mix(in srgb,var(--teal) 10%,white);color:var(--teal-dark)}:focus-visible{outline:2px solid var(--teal);outline-offset:3px}pre,code{font-family:ui-monospace,monospace;font-size:12px}code{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--paper);padding:14px;border-radius:var(--radius);max-height:280px;overflow:auto}.result{border-color:var(--teal);background:color-mix(in srgb,var(--teal) 5%,white)}.error{border-color:var(--danger);background:color-mix(in srgb,var(--danger) 5%,white)}.split{display:grid;grid-template-columns:2fr 1fr;gap:22px}.split>*,.grid>*{min-width:0}.row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.new{background:color-mix(in srgb,var(--teal) 10%,white);color:var(--teal);padding:3px 8px;border-radius:var(--radius)}.compact{margin:8px 0}.spark{width:100%;height:140px;max-width:100%;overflow:visible}.footer{color:var(--muted);font-size:11px;margin-top:32px}.copy{padding:2px 5px;font-family:ui-monospace,monospace;background:var(--paper);color:var(--muted);border-color:var(--line)}.copy:hover{color:white}.targets td{padding:8px}.target-handle{display:inline-block;max-width:180px;overflow:hidden;text-overflow:ellipsis;vertical-align:middle}.target-meta{font-size:11px;color:var(--muted)}.targets .row{flex-wrap:nowrap;justify-content:start}.poster{grid-template-columns:repeat(3,minmax(0,1fr))}.poster .hero{grid-column:span 2;background:var(--teal-dark);color:white}.hero .metric{font-size:80px}.hero .muted,.hero .eyebrow{color:inherit;opacity:.7}.poster .freshness{grid-column:1/-1}.hero details{color:var(--foreground);background:white;padding:10px;border-radius:var(--radius)}summary{cursor:pointer;color:var(--teal);font-size:13px;font-weight:600}details+details{margin-top:10px}.hero summary{color:var(--teal)}section[id]{scroll-margin-top:20px}.highlight{background:color-mix(in srgb,var(--teal) 8%,white)}.timeline-list{max-height:200px;overflow:auto}.target-tools{margin-bottom:14px}.target-tools input{flex:1}time{white-space:nowrap}.pending-submit{opacity:.65;cursor:wait}@media(max-width:850px){.split{grid-template-columns:1fr}header{padding:18px;gap:16px;flex-wrap:wrap}header span{display:none}main{padding:24px 16px}h1{font-size:30px}.poster{grid-template-columns:1fr}.poster .hero,.poster .freshness{grid-column:auto}.row{align-items:start}.card{padding:18px}nav{gap:18px}.targets .row{flex-wrap:wrap}}@media(prefers-reduced-motion:reduce){button,a{transition:none}}.grid,.split{align-items:start}.actions{display:flex;align-items:center;justify-content:flex-start;gap:8px;flex-wrap:wrap}.section-gap{margin-top:24px}.row-actions{display:flex;gap:8px;align-items:center;flex-wrap:nowrap}nav{flex-wrap:wrap;gap:16px}nav a{white-space:nowrap}nav a[aria-current=page]{opacity:1;text-decoration:underline;text-underline-offset:7px}.breadcrumb{display:flex;gap:8px}.result,.error{padding:14px 18px}.result p,.error p{margin:4px 0}.result-links{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:10px}.result>details{margin-top:12px}a.badge,a:has(>.badge){text-decoration:underline;text-underline-offset:3px}tbody tr:hover{background:color-mix(in srgb,var(--teal) 10%,white)}input[type=checkbox]{accent-color:var(--teal)}input::file-selector-button{background:var(--paper);color:var(--teal);border:0;border-radius:var(--radius);padding:6px 10px;margin-right:8px;cursor:pointer}details[open]>:not(summary){animation:reveal .16s ease-out}@keyframes reveal{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}.target-tools{display:grid;grid-template-columns:minmax(100px,1fr) auto auto auto}.target-tools input,.target-tools select{width:100%;max-width:none}.table-hint{display:none;font-size:11px;color:var(--muted)}.table-hint.overflowing{display:block}.budget-row,.alert-group,.rejected-row,.receipt-group{padding:14px 0;border-bottom:1px solid var(--line)}.receipt-detail{padding:8px 0}.receipt-detail pre{width:100%;max-height:340px}.rejected-row .row{align-items:start}.budget-row progress{width:100%;height:7px;accent-color:var(--teal)}.budget-row summary{font-weight:400}.cadence-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}.cadence-strip p{min-height:23px;margin:8px 0 0}[data-preview]>label{margin:18px 0 12px}.preview-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px}.preview-actions h3{margin:0 auto 0 0}.hover-ring,.spark-tooltip{opacity:0;pointer-events:none}.spark-point:hover .hover-ring,.spark-point:focus .hover-ring,.spark-point:hover .spark-tooltip,.spark-point:focus .spark-tooltip{opacity:1}.spark:has(.spark-point:hover) .last-value{opacity:0}.mini-spark{height:65px}.summary-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:0 0 26px}.summary-strip strong{display:block;font-size:30px;letter-spacing:-1px}.summary-strip a{padding:12px 14px;border:1px solid var(--line);border-radius:var(--card-radius)}.utility-disclosure{margin:0 0 20px}.first-run{background:var(--teal-dark);color:white;padding:36px}.first-run a{color:white}.first-run p{max-width:520px}.zero-match{margin:12px 0}.row-count{font-size:12px}.identity{font-size:11px;color:var(--muted)}@media(max-width:650px){.target-tools{grid-template-columns:1fr 1fr}.target-tools input{grid-column:1/-1}.target-tools button{width:100%}.summary-strip{gap:10px;font-size:12px}.summary-strip strong{font-size:26px}.cadence-strip{grid-template-columns:1fr}.row-actions{gap:6px}nav{gap:14px;font-size:13px}.first-run{padding:24px}}@media(prefers-reduced-motion:reduce){details[open]>:not(summary){animation:none}}`;
export const functionCss = `
.function-page main{max-width:1236px;padding-top:24px}.function-view{min-width:0}.function-view :focus-visible,.function-list :focus-visible{outline:2px solid var(--teal);outline-offset:3px}.function-heading{padding-top:0;display:flex;justify-content:space-between;gap:16px;align-items:center}.function-view h1{display:block;font-size:32px;margin:8px 0 12px}.function-identity{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.function-meta{font-size:12px;color:var(--muted)}.flow-line{display:flex;gap:8px;flex-wrap:wrap;font-size:12px;margin:14px 0 8px}.function-fact{margin:8px 0 18px}.function-controls{display:flex;gap:10px;align-items:center;margin-bottom:22px}.function-controls .button{padding:10px;border-radius:6px}.function-menu{position:relative}.function-menu>summary{font-size:22px;list-style:none;padding:2px 14px;border:1px solid var(--line);border-radius:6px;background:var(--surface)}.function-menu-body{position:absolute;z-index:30;right:0;background:var(--surface);padding:16px;border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 32px #182b3520;min-width:220px;display:grid;gap:12px}.mobile-meta{display:none}.function-menu-body .function-meta{display:block}.function-menu-body .rights-chip{display:inline-flex}.function-view .function-grid{display:grid;grid-template-columns:7fr 5fr;gap:20px;margin-bottom:20px}.function-view .function-grid>.card{min-width:0;margin:0}.function-view h2{font-size:16px;margin-bottom:8px}.strip-legend{display:flex;gap:4px;flex-wrap:wrap}.strip-legend button{border:0;font-size:11px;padding:4px;background:none;color:var(--muted)}#target-strip{display:flex;gap:4px;flex-wrap:wrap;margin:18px 0}.cell{display:block;width:12px;height:26px;border-radius:3px;background:var(--teal)}.cell.s-unchanged{background:#7aaba4}.cell.s-failed,.cell.s-gone{background:var(--danger)}.cell.s-parked{background:var(--warn)}.cell.s-not-read{background:var(--line)}.target-caption,.run-summary{font-size:12px}.run-bars{display:flex;align-items:end;gap:5px;height:105px;margin:10px 0 12px}.run-bar{display:block;flex:1;min-width:0;height:var(--bar-height);background:var(--teal);border-radius:3px 3px 0 0}.run-bar.failed{background:var(--danger)}.run-bar.partial{background:var(--warn)}.run-bar.zero{background:var(--line);height:3px}.run-window select{padding:3px 6px;font-size:12px}.run-bar:hover,.run-bar:focus-visible,.cell:hover,.cell:focus-visible{transform:scale(1.08);filter:brightness(1.12)}.run-bars:has(a:hover)>a:not(:hover),#target-strip:has(a:hover)>a:not(:hover){opacity:.6}.cell,.run-bar{transition:transform .15s,opacity .15s}#hover-card{position:fixed;z-index:1000;background:var(--surface);border:1px solid var(--line);box-shadow:0 10px 40px #182b3530;border-radius:10px;padding:14px;width:280px;max-width:calc(100vw - 24px);font-size:12px}#hover-card p{margin:0 0 10px}.hover-close{display:none}.row-tabs{display:flex;gap:8px;max-width:100%;flex-wrap:wrap}.row-tabs a{font-size:12px;padding:6px;border-radius:6px;overflow-wrap:anywhere}.row-tabs a[aria-selected=true]{background:var(--paper);color:var(--foreground)}.latest-table{overflow:auto;max-width:100%}.latest-table th button{font-size:11px;border:0;background:none;padding:0;color:var(--muted)}.latest-table td{max-width:250px;overflow:hidden;text-overflow:ellipsis}.latest-mobile{display:none}.rows-links{font-size:12px;margin-top:18px}.row-sheet{width:680px;max-width:calc(100vw - 32px);max-height:85vh;border:1px solid var(--line);border-radius:10px;padding:20px;color:var(--foreground)}.row-sheet::backdrop{background:#182b3560}.row-sheet form{justify-content:end}.row-sheet dl{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:12px;font-size:13px}.row-sheet dd{margin:0;overflow-wrap:anywhere}.row-sheet dt{color:var(--muted);overflow-wrap:anywhere}.columns-choice{position:relative}.columns-choice[open]{max-height:260px;overflow:auto}.columns-choice label{margin:8px}.problem-line{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 0}.function-view .utility-disclosure{padding:14px 20px;margin-bottom:10px}.target-list{display:grid;gap:6px;margin:14px 0}.target-list>a{display:flex;justify-content:space-between;gap:12px;padding:10px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.target-list span{font-size:12px;color:var(--muted)}.failure-group+.failure-group{border-top:1px solid var(--line);margin-top:16px;padding-top:16px}.failure-group h2{font-size:15px;margin:0 0 8px;overflow-wrap:anywhere}.failure-group p{font-size:12px;overflow-wrap:anywhere}.failure-message{line-height:1.5;max-height:18em}.failure-group details{margin-top:12px}.function-list>label{margin:20px 0}.function-list>section{margin:28px 0}.function-list .function-grid{grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr))}.function-list a.function-card{display:block;color:var(--foreground);text-decoration:none}.function-list a.function-card:hover{background:color-mix(in srgb,var(--teal) 4%,var(--surface));border-color:var(--teal)}.function-list .function-card code{color:var(--muted)}.function-list .function-heading>.badge{position:static}.function-list .function-card h2{font-size:17px;margin:0}.daily-bars{height:56px;display:flex;align-items:end;gap:5px;margin-top:12px}.daily-bars span{background:var(--teal);flex:1;border-radius:2px}.sr-only{position:absolute;width:1px;height:1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@media(max-width:650px){.function-page main{padding:20px 16px}.function-page header{padding:12px 16px}.function-page header nav{display:none}.function-page header strong{font-size:14px}.function-view h1{font-size:28px}.function-meta{display:none}.function-identity .rights-chip{display:none}.flow-line{display:none}.function-fact{font-size:13px}.function-controls>form{flex:1}.function-controls>form>button{width:100%}.function-controls button,.function-menu>summary{min-height:44px}.function-controls{margin-top:16px}.function-view .function-grid{grid-template-columns:1fr;gap:16px}.function-view .card{padding:16px}.desktop-actions{display:none}.mobile-meta{display:block;font-size:12px}.latest-table{display:none}.latest-mobile{display:grid;gap:10px}.row-card{white-space:normal;text-align:left;width:100%;padding:14px;color:var(--foreground)}.row-card>strong{display:block;margin-bottom:10px}.row-card>span{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.row-card small{display:block;color:var(--muted);font-size:10px}.row-card span{overflow-wrap:anywhere}.row-sheet{margin:auto 0 0;max-width:100%;width:100%;max-height:80vh;border-radius:16px 16px 0 0}.row-sheet dl{grid-template-columns:1fr}.row-tabs{margin-bottom:10px}.target-list>a{display:grid}.function-list .function-heading{flex-wrap:wrap}.strip-legend button{min-height:32px}}@media(pointer:coarse){#hover-card{left:0!important;top:auto!important;bottom:0;width:100%;max-width:100%;border-radius:16px 16px 0 0;padding:24px;font-size:14px}.hover-close{display:inline-block;float:right;min-height:44px}}@media(prefers-reduced-motion:reduce){.cell,.run-bar{transition:none}}
`;
export const visualCss = round4Css;
export const Badge = ({ value }: { value: unknown }) => (
  <span class={`badge ${String(value)}`}>{String(value ?? "—")}</span>
);
export function Layout({
  title,
  subtitle,
  children,
  titleMono,
  functionPage = false,
}: {
  title: string;
  subtitle: string;
  children: unknown;
  titleMono?: boolean;
  functionPage?: boolean;
}) {
  return html`<!doctype html>${(
      <html lang="en">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width,initial-scale=1" />
          <title>{title} · MDP</title>
          <link rel="stylesheet" href="/style.css" /><style dangerouslySetInnerHTML={{__html:round4Css}}/><style dangerouslySetInnerHTML={{__html:primitivesCss+functionCss}}/>
          <link rel="icon" type="image/svg+xml" href="/favicon.ico" />
          <script src="/ui.js" defer />
        </head>
        <body class={functionPage ? "function-page" : undefined}>
          <header>
            <div>
              <strong>Console</strong>
              <span>Console · {process.env.MDP_AUTH_MODE === "dev" ? "local" : "authenticated"}</span>
            </div>
            <nav>
              <a href="/ops" aria-current={title === "Ops" ? "page" : undefined}>Ops</a>
              <a href="/screen/weekly" aria-current={title === "Weekly overview" ? "page" : undefined}>Weekly overview</a>
              <a href="/functions" aria-current={title === "Functions" || subtitle.includes("function ·") ? "page" : undefined}>Functions</a>
              <a href="/tenants" aria-current={title === "Tenants" ? "page" : undefined}>Tenants</a>
              <a href="/reference" aria-current={title === "Reference" ? "page" : undefined}>Reference</a>
              <a href="/explorer" aria-current={title === "Explorer" ? "page" : undefined}>Explorer</a>
              <a href="/sandboxes">Sandbox</a>
              <a href="/workbench" aria-current={title === "Workbench" ? "page" : undefined}>Workbench</a>
              <a href="/queries" aria-current={title === "Query review" ? "page" : undefined}>Queries</a>
            </nav>
          </header>
          <main>
            <div hidden={functionPage} class="eyebrow breadcrumb">{subtitle.includes("function ·") ? <><a href="/functions">Functions</a><span>/</span><span>{title}</span></> : title === "Ops" ? <span>Ops</span> : <><a href="/ops">Ops</a><span>/</span><span>{title}</span></>}</div>
            <h1 hidden={functionPage} class={titleMono ? "title-mono" : undefined}>{title}</h1>
            {subtitle&&<p class="lead">{subtitle}</p>}
            {children}
            <div class="footer">
              MDP · Audited ·{" "}
              {process.env.CLERK_SECRET_KEY
                ? "Clerk authentication"
                : process.env.MDP_AUTH_MODE === "dev"
                  ? "Local development identity"
                  : "Machine authentication"}
            </div>
          </main>
        </body>
      </html>
    )}`;
}
function Value({ value }: { value: unknown }) {
  if (value === "Needs ID · invalid for activation") return <span class="badge warning" aria-invalid="true">Needs ID · invalid for activation</span>;
  if (value === null || value === undefined)
    return <span class="muted">—</span>;
  if (typeof value === "object") return <details><summary>View details</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
  if (typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(value))
    return <CopyChip value={value} label={value.slice(0,8)}/>;
  if (typeof value === "string" && /^\d{4}-\d\d-\d\dT/.test(value)) {
    const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 60000));
    return <RelativeTime at={value}/>;
  }
  return <>{String(value)}</>;
}
export function Table({
  rows,
  empty = "No records yet",
  nextStep,
  expanded = false,
  hiddenColumns = [],
  limit = 10,
}: {
  rows: Record<string, unknown>[];
  empty?: string;
  nextStep: NextStep;
  expanded?: boolean;
  hiddenColumns?: string[];
  limit?: number;
}) {
  if (!rows.length) return <EmptyState message={empty} nextStep={nextStep}/>;
  const cols = Object.keys(rows[0] ?? {});
  const visible = rows;
  return (
    <div data-table data-limit={expanded ? rows.length : limit}>
      <p class="muted compact row-count" data-row-count>{rows.length} rows</p>
      <p class="table-hint">More columns →</p>
      <div class={`table-wrap ${expanded ? "expanded-table" : ""}`}>
      <table>
        <thead>
          <tr>
            {cols.map((c) => (
              <th hidden={hiddenColumns.includes(c)}>{c.replaceAll("_", " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visible.map((row,i) => (
            <tr hidden={!expanded && i >= limit} data-table-row class={row.platform_account_id === "Needs ID · invalid for activation" ? "invalid-import" : undefined}>
              {cols.map((c) => (
                <td hidden={hiddenColumns.includes(c)}>
                  {[
                    "status",
                    "coverage",
                    "severity",
                    "resolution_status",
                  ].includes(c) ? (
                    <Badge value={row[c]} />
                  ) : (
                    <><Value value={row[c]} />{c === "message" || c === "error_message" ? <div class="event-label">{isRefusal(row) ? <RefusalPopover code={String(row.error_class ?? row.event_type ?? "unmapped")} message={String(row[c] ?? "")}/> : <EventLabel type={String(row.event_type ?? "")}/>}</div> : null}</>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {!expanded && rows.length > limit ? <button type="button" class="secondary" data-show-all>Show all {rows.length} rows</button> : null}
    </div>
  );
}
function Hidden({ name, value }: { name: string; value: string }) {
  return <input type="hidden" name={name} value={value} />;
}
function ButtonForm({
  action,
  label,
  values,
  back = "/ops",
  secondary = false,
  disabled = false,
}: {
  action: string;
  label: string;
  values: Record<string, string>;
  back?: string;
  secondary?: boolean;
  disabled?: boolean;
}) {
  return (
    <form method="post" action={`/actions/${action}`} data-confirm={action === "runner" ? "Change runner? The next cycle uses this runner." : action === "pause" && values.enabled === "false" ? "Pause this function? Scheduled runs stop until you resume it." : undefined}>
      <Hidden name="back" value={back} />
      {Object.entries(values).map(([k, v]) => (
        <Hidden name={k} value={v} />
      ))}
      <button disabled={disabled} class={secondary ? "secondary" : ""} title="Tab to focus, Enter to run">{label}{["run","retry"].includes(action)?<> <kbd>↵</kbd></>:null}</button>
    </form>
  );
}
export function Result({ result, preview }: { result: unknown; preview?: {token:string; rows:Record<string,unknown>[]; errors:string[]} | undefined }) {
  if (result === undefined) return null;
  const value = z.record(z.string(), z.unknown()).parse(result);
  const payload = z.record(z.string(), z.unknown()).safeParse(value.result);
  const data = payload.success ? payload.data : value;
  const failed = value.state === "failed" || Boolean(data.error_class);
  const input = z.record(z.string(), z.unknown()).safeParse(value.input);
  const actionName = String(value.action ?? "Operation");
  const summary = failed ? String(data.message ?? value.message ?? value.error_class ?? "Action failed")
    : actionName === "alerts.acknowledgeGroup" ? `Acknowledged ${data.acknowledged} alerts · ${data.alert_class} · ${data.source_key}`
    : actionName === "targets.bulkActivate" ? `Targets ${input.success && input.data.active ? "activated" : "deactivated"}.`
    : actionName === "targets.undoImport" ? `Undid import of ${data.count} unused targets.`
    : typeof data.count === "number" && typeof data.adds === "number" ? `${data.dry_run ? "Dry run · " : ""}${data.adds} to add · ${String(data.updates)} to update · ${String(data.unchanged)} unchanged${data.dry_run ? " · verify identity before activation" : ""}`
    : typeof data.count === "number" ? data.dry_run ? `Dry run · ${data.count} parsed · verify identity before activation` : `Imported ${data.count} pending targets.`
    : data.path === "cloud" ? "dbt Cloud job queued."
    : data.run_id ? "Core run started."
    : actionName === "budgets.create" ? "Budget created."
    : actionName === "budgets.raise" ? "Budget raised."
    : actionName === "targets.createSet" ? "Target set created."
    : actionName === "reference.reimport" ? `Re-import requested for ${String(data.source ?? "the source")}; the next refresh run picks it up.`
    : `${actionName.replaceAll(".", " · ")} completed.`;
  const retryForms:Record<string,{action:string; fields:string[]}>={
    'streamlines.dryProbe':{action:'probe',fields:['source_key','scope']},
    'streamlines.runNow':{action:'run',fields:['source_key','key','scope']},
    'streamlines.unpark':{action:'unpark',fields:['source_key']},
    'dbt.retry':{action:'retry',fields:['cycle_id']},
    'dbt.replay':{action:'replay',fields:['cycle_id']},
    'targets.resolve':{action:'resolve',fields:['id','platform_account_id']},
    'alerts.acknowledge':{action:'ack-alert',fields:['id']},
    'alerts.resolve':{action:'resolve-alert',fields:['id']},
    'streamlines.acknowledgeDrift':{action:'drift',fields:['source_key','fingerprint']},
  };
  const retry=retryForms[actionName];
  const retryValues=retry&&input.success&&retry.fields.every(k=>typeof input.data[k]==='string'&&input.data[k]!=='[redacted]')
    ? Object.fromEntries(retry.fields.map(k=>[k,String(input.data[k])])) : null;
  return <Notice tone={failed?"error":"result"} nextStep={{label:"Ops",href:"/ops"}}>
    <div class="row"><p><Badge value={failed ? "failed" : data.status ?? "succeeded"} /> {summary}</p><button type="button" class="secondary" data-dismiss>Dismiss</button></div>
    {failed&&retry&&retryValues&&<ButtonForm action={retry.action} label="Retry action" values={retryValues}/>}
    {failed&&<RefusalPopover code={String(data.error_class??value.error_class??"unmapped")} message={summary} auditId={typeof value.audit_id==="string"?value.audit_id:undefined}/>}
    {preview ? <>{preview.errors.map(error=><p class="warning">{error}</p>)}<p>{preview.rows.filter(row=>String(row.platform_account_id??"").trim()).length} valid · {preview.rows.filter(row=>!String(row.platform_account_id??"").trim()).length} needs id</p><p class="muted">Paste saved for 30 min</p><Table nextStep={{label:"View functions",href:"/functions"}} rows={preview.rows.map(row=>({...row,platform_account_id:row.platform_account_id || "Needs ID · invalid for activation"}))} empty="No valid rows"/></> : Array.isArray(data.rows) ? <Table nextStep={{label:"View functions",href:"/functions"}} rows={z.array(z.record(z.string(), z.unknown())).parse(data.rows)} /> : null}
    <div class="result-links">{actionName === "alerts.acknowledgeGroup" && <><a href={`/ops?alert_class=${encodeURIComponent(String(data.alert_class))}#alerts`}>Review this alert class →</a>{data.source_key !== "Platform" && <a href={`/functions/${String(data.source_key)}`}>View function →</a>}</>}{preview && !preview.errors.length && data.dry_run ? <ButtonForm action="import" label={`Import targets · ${preview.rows.filter(row=>String(row.platform_account_id??" ").trim()).length} valid · ${preview.rows.filter(row=>!String(row.platform_account_id??" ").trim()).length} needs id`} values={{preview_token:preview.token,dry_run:"false"}}/> : null}{actionName !== "alerts.acknowledgeGroup" && typeof data.run_id === "string" ? <a href={`/runs/${data.run_id}`} data-live-run={data.status === "queued" || data.status === "running" ? "true" : undefined}>Open run, receipts and trace →</a> : null}
    {actionName !== "alerts.acknowledgeGroup" && input.success && typeof input.data.source_key === "string" ? <a href={`/functions/${input.data.source_key}`}>View function →</a> : null}
    {!failed && actionName === "targets.importTargets" && !data.dry_run ? <div class="row"><a href="/ops?resolution=pending#targets">Review imported pending targets →</a>{typeof value.audit_id === "string" ? <ButtonForm action="undo-import" label="Undo unused import" secondary values={{audit_id:value.audit_id}}/> : null}</div> : null}
    {typeof value.audit_id === "string" ? <a href={`/audit/${value.audit_id}`}>View audit →</a> : null}
    </div><details><summary>Raw result</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
  </Notice>;
}
export async function tenantsPage(context: Context, result?: unknown, keySlug?: string) {
  const client = createRouterClient(router, { context });
  const tenants = await client.tenants.list({});
  const keyTenant = tenants.find(t => t.slug === keySlug);
  const keyCommand = keyTenant ? `pnpm --dir control mdp keys create --tenant ${keyTenant.slug}` : "";
  return Layout({ title: "Tenants", subtitle: "", children: <>
    <Result result={result}/>
    <section class="card">
      <h2>New tenant</h2>
      <p class="muted">The slug stays fixed.</p>
      <form method="post" action="/actions/create-tenant">
        <Hidden name="back" value="/tenants"/>
        <label>Slug<input name="slug" pattern="[a-z]([a-z0-9]|-)*" placeholder="sample-tenant" required/></label>
        <label>Name<input name="name" required/></label>
        <button>Create tenant</button>
      </form>
    </section>
    <section class="card">
      <h2>Tenants · {tenants.length}</h2>
      {!tenants.length ? <p class="empty">No tenants yet. Create one above.</p> : <div class="table-wrap"><table>
        <thead><tr><th>Tenant</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>{tenants.map((t, i) => {

          return <tr><td><strong>{t.name}</strong><div class="muted">{t.slug}</div></td><td><Badge value={t.status}/></td>
            <td><div class="actions"><a href={`#tenant-${t.slug}`}>Edit</a><a href={`/tenants?key=${t.slug}#issue-key`}>Issue key →</a></div></td></tr>;
        })}</tbody>
      </table></div>}
    </section>
    {keyTenant && <section class="card" id="issue-key">
      <h2>Issue a key · {keyTenant.slug}</h2>
      <p>Run from the repository root with your admin CLI connection. Store the returned api_key immediately; it is shown once.</p>
      <pre>{keyCommand}</pre><button type="button" class="secondary" data-copy={keyCommand}>Copy key command</button>
      {keyTenant.status !== "active" && <p class="muted">Activate the tenant before issuing a key.</p>}
    </section>}
    {tenants.map((t, i) => {

      return <section class="card" id={`tenant-${t.slug}`}>
        <h2>{t.slug}</h2>
        <form method="post" action="/actions/patch-tenant">
          <Hidden name="back" value="/tenants"/><Hidden name="id" value={t.id}/>
          <label>Name<input name="name" value={t.name} required/></label>
          <label>Status<select name="status"><option selected={t.status === "active"} value="active">Active</option><option selected={t.status === "inactive"} value="inactive">Inactive</option></select></label>
          <button class="secondary">Save tenant</button>
        </form>

      </section>;
    })}
  </> });
}
const humanize = (value:string) => { const text=value.replaceAll('_',' '); return text.charAt(0).toUpperCase()+text.slice(1); };
function statusAge(seconds: number) {
  return seconds < 3600 ? `${Math.floor(seconds / 60)}m` : seconds < 86400 ? `${(seconds / 3600).toFixed(1)}h` : `${(seconds / 86400).toFixed(1)}d`;
}
export function StatusCard({ status, failures=[] }: { failures?:Failure[]; status: PlatformStatus & { secrets_health?: {state:string;message?:string} } }) {
  const tone = status.verdict === "broken" ? "count-danger" : status.verdict === "attention" ? "count-warn" : "count-muted";
  return <section class="card" id="status">
    <div class="row"><h2><a href="/status">Platform status</a></h2><strong class={tone}>{status.verdict}</strong></div>
    <p class="muted">Checked <RelativeTime at={status.checked_at}/> · <a href="/status">Refresh status →</a></p>
    <div class="cadence-pills">{status.cadences.map(row=><CadencePill cadence={row.cadence} scope={row.scope} failed={failures.some(f=>row.open.some(c=>c.id===f.cycle_id))} overdue={row.overdue} interval={row.interval_seconds} {...row.last_closed?{lastAge:row.last_closed.age_seconds}:{}} {...row.open.length?{openAge:Math.max(...row.open.map(c=>c.age_seconds))}:{}}/>)}</div>
    <div class="table-wrap"><table><thead><tr><th>Cadence / scope</th><th>Last close</th><th>Age</th><th>State</th><th>Build</th><th>Open cycles</th></tr></thead>
      <tbody>{status.cadences.map(row => <tr>
        <td>{row.cadence}<div class="muted">{row.scope}</div></td>
        <td>{row.last_closed ? <span title={row.last_closed.closed_at ?? ""}>#{row.last_closed.close_no ?? "unassigned"}</span> : "—"}</td>
        <td>{row.last_closed ? statusAge(row.last_closed.age_seconds) : "—"}</td>
        <td class={row.overdue || !row.last_closed ? "count-warn" : "count-muted"}>{!row.last_closed || row.overdue ? <a href="/runbooks/runners-held">{row.last_closed ? "Overdue" : "No close yet"} · Check runners →</a> : "Current"}</td>
        <td><details><summary>{row.last_closed?.git_sha?.slice(0, 12) ?? "Unknown"}</summary><p>{row.last_closed?.git_sha ?? "Unknown SHA"}</p><p>{row.last_closed?.image_digest ?? "Unknown image"}</p></details></td>
        <td>{row.open.length ? row.open.map(c => <div title={c.id}>{c.id.slice(0, 8)} · {statusAge(c.age_seconds)}</div>) : "None"}</td>
      </tr>)}</tbody></table></div>
    {status.alerts.length ? <div>{status.alerts.map(a => <div class="alert-group row"><span><Badge value={a.severity}/> <a href={`/ops?alert_class=${encodeURIComponent(a.class)}#alerts`}>{a.class}</a> · {a.count}</span><span>{a.runbook_urls.map(url => <a href={url}>{url.split("/").at(-1)} → </a>)}{a.no_guide && "no guide"}</span></div>)}</div> : <EmptyState message="No unacknowledged, unresolved alerts." nextStep={{label:"View alert history",href:"/ops?resolved=true#alerts"}}/>}
    <p>Retry launcher: <Badge value={status.retry_launcher.state}/></p>
    <p>{status.retry_launcher.message} <a href="/runbooks/cadence-failed">Recovery guide →</a></p>
    {status.secrets_health&&<p>Secrets health · <Badge value={status.secrets_health.state}/> {status.secrets_health.message}</p>}
    <p>Heartbeat: {status.heartbeat ? <>{status.heartbeat.configured ? status.heartbeat.action.replace("heartbeat.", "") : "not configured"} · {status.heartbeat.cadence} · {statusAge(status.heartbeat.age_seconds)} ago.</> : "No scheduled build has recorded a heartbeat."} <a href="/runbooks/service-unreachable#external-heartbeat">Set MDP_HEARTBEAT_URL and check delivery →</a></p>
    <p>Alert email · last 24 h: <strong>{status.sends.failed}</strong> failed attempts · <strong>{status.sends.pending}</strong> pending · {status.sends.skipped} skipped · {status.sends.gave_up} abandoned</p>
    <p>API builds: <strong>{status.versions.state}</strong> · control <span title={status.versions.control_sha ?? ""}>{status.versions.control_sha?.slice(0,12) ?? "unknown"}</span> · data <span title={status.versions.data_sha ?? ""}>{status.versions.data_sha?.slice(0,12) ?? "unknown"}</span></p>
    {status.reasons.length > 0 && <ul>{status.reasons.map(reason => <li>{reason}{reason.includes("close") && <a href="/runbooks/runners-held"> · Check runners →</a>}</li>)}</ul>}
    {!emailTransport() && (
      <p>
        Alert email delivery is not set up. Alerts stay in Ops.{' '}
        <a href="/runbooks/service-unreachable#alert-email">Set up alert email →</a>
      </p>
    )}
    <details><summary>How status is decided</summary>{Object.entries(status.rules).map(([verdict, rule]) => <p><strong>{verdict}</strong>: {rule}</p>)}<p>Cadence intervals: hourly 1 h, daily 24 h, weekly 7 d. Pending emails are unsent alerts opened or retried in the last 24 h. Cycle builds describe their opener; API versions describe running code, not mart freshness.</p></details>
  </section>;
}
export async function statusPage(context: Context) {
  const status = await createRouterClient(router, { context }).status({});
  return Layout({ title: "Platform status", subtitle: "Cycles, alerts, delivery and API builds", children: <StatusCard status={status} failures={await consoleFailures(context.db)}/> });
}
export async function opsPage(context: Context, result?: unknown, query: Record<string, string> = {}) {
  const c = createRouterClient(router, { context });
  const [streamlines, sets, targets, budgets, alerts, runner, recentRuns, status] =
    await Promise.all([
      c.streamlines.list({}),
      c.targets.listSets({}),
      c.targets.list({}),
      c.budgets.list({}),
      attention(context.db,query.resolved === "true",query.acknowledged === "true"),
      c.dbt.runnerMode.get({}),
      c.runs.list({}),
      c.status({}),
    ]);
  const [spend, pauses, recovery] = await Promise.all([budgetSpend(context.db),pausedSince(context.db),recoveryChoices(context.db)]);
  const importPreview = query.preview ? readPreview(query.preview,context.identity.actor) : undefined;
  const csvPreview = importPreview ? inspectTargetCsv(importPreview.csv) : undefined;
  const health = await targetHealth(context.db,targets.map(t=>t.id));
  const driftPages = new Map(await Promise.all([...new Set(alerts.filter(a=>a.class==="schema_drift"&&a.source_key).map(a=>a.source_key!))].map(async key=>[key,await c.functions.page({source_key:key,metadata_only:true}).then(p=>driftHistory(p.fingerprint_history),()=>[])] as const)));
  const acknowledgedAlerts = await attention(context.db, false, true);
  const openAlerts=alerts.filter(a=>!a.acknowledged_by);
  const visibleAlerts=alerts.filter(a=>(!query.alert_class || a.class===query.alert_class) && (query.resolved==='true' || query.acknowledged==='true' || !a.acknowledged_by));
  const alertTone=(items:typeof alerts)=>items.length ? items.some(a=>a.severity==='critical')?'count-danger':'count-warn':'count-muted';
  const alertGroups = new Map<string,typeof alerts>();
  for (const alert of visibleAlerts) {
    const key=`${alert.class} · ${alert.source_key ?? 'Platform'}`;
    alertGroups.set(key,[...(alertGroups.get(key) ?? []),alert]);
  }
  const matchingTargets = targets.filter(t => (!query.q || `${t.id} ${t.handle} ${t.platform_account_id} ${t.display_name}`.toLowerCase().includes(query.q.toLowerCase())) && (!query.resolution || t.resolution_status === query.resolution) && (!query.active || String(Boolean(t.activated_at && !t.deactivated_at)) === query.active))
    .sort((a,b) => (a.handle ?? a.id).localeCompare(b.handle ?? b.id) * (query.sort === "desc" ? -1 : 1));
  const offset = Math.max(0, Number(query.target_offset) || 0);
  const targetPage = matchingTargets.slice(offset, offset + 25);
  return Layout({
    title: "Ops",
    subtitle: "",
    children: (
      <>
        <StatusCard status={status} failures={await consoleFailures(context.db)}/>
        <Result result={result} preview={csvPreview && query.preview ? {...csvPreview,token:query.preview} : undefined}/>
        <p class="muted"><a href="/ops?acknowledged=true#alerts">{acknowledgedAlerts.length} acknowledged alerts await recovery →</a> · <a href="/ops#alerts">Review open alerts →</a></p>
        <div class="summary-strip"><a href="#functions"><strong>{streamlines.filter(s=>s.enabled).length}</strong>Functions enabled</a><a href="#alerts"><strong class={query.resolved==="true"?"count-muted":alertTone(openAlerts)}>{alerts.length}</strong>{query.resolved === "true" ? "Resolved" : query.acknowledged === "true" ? "Acknowledged" : "Open"} alerts</a><a href="#targets"><strong class={targets.some(t=>t.resolution_status==='pending')?"count-warn":"count-muted"}>{targets.filter(t=>t.resolution_status==='pending').length}</strong>Targets awaiting review</a></div>
        <section class="card" id="functions">
          <div class="row">
            <h2>Functions</h2>
            <Badge value={runner.runner} />
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Function</th>
                  <th>Cadence</th>
                  <th>State</th>
                  <th>Outputs</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {streamlines.map((s) => (
                  <tr>
                    <td>
                      <EntityName slug={s.source_key} href={`/functions/${s.source_key}`}/>
                      <div class="muted">{s.layer}{!s.enabled ? <> · paused since <Value value={pauses.find(p=>p.source_key===s.source_key)?.at ?? "not recorded"}/></> : null}</div>
                    </td>
                    <td>{s.cadence_tag}</td>
                    <td>
                      <Badge value={s.enabled ? "active" : "paused"} />{" "}
                      {recentRuns.filter(r=>r.streamline_id === s.id).slice(0,1).map(r=><div class="function-run"><Badge value={r.status}/><div><RunRef id={r.id} at={r.created_at}/></div></div>)}
                    </td>
                    <td>{s.writes.length ? <div class="output-chips">{s.writes.map(relation=><EntityPopover label={<EntityName slug={relation}/>} summary={`${relation} · ${s.layer}${s.storage ? ` · ${s.storage}` : ''}`} facts={{format:s.storage==='heap'?'table':s.storage==='iceberg'?'file':'model',...(s.storage==='heap'?{location:'postgres'}:s.storage==='iceberg'?{location:'r2'}:{}),details:[{label:'Layer',value:s.layer},{label:'Storage',value:s.storage},{label:'External',value:s.external?'Yes':'No'}],links:[{label:'Function',href:`/functions/${s.source_key}`}]}}/>)}</div> : "Control operation"}</td>
                    <td>
                      <div class="row-actions">
                        <ButtonForm
                          action="run"
                          back={`/functions/${s.source_key}`}
                          label={s.tenant_bound && !context.identity.tenant_id ? "Tenant session required" : "Run now"}
                          disabled={s.tenant_bound && !context.identity.tenant_id}
                          values={{
                            source_key: s.source_key,
                            key: randomUUID(),
                            scope: s.tenant_bound ? `tenant:${context.identity.tenant_id ?? ""}` : "global",
                          }}
                        />
                        <ButtonForm
                          secondary
                          action="pause"
                          label={s.enabled ? "Pause" : "Resume"}
                          values={{
                            source_key: s.source_key,
                            enabled: String(!s.enabled),
                          }}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <div class="split">
          <section class="card targets" id="targets">
            <h2>Targets <span class="muted">· {targets.length} total{query.q || query.resolution || query.active ? ` · ${matchingTargets.length} matching` : ""} · {targets.filter(t=>t.activated_at && !t.deactivated_at).length} active</span></h2>
            <form method="get" action="/ops#targets" class="target-tools">
              <input name="q" aria-label="Search targets" placeholder="Search targets" value={query.q ?? ""} />
              <select name="resolution" aria-label="Resolution filter"><option value="">All states</option>{["pending","resolved","failed"].map(v=><option selected={query.resolution === v}>{v}</option>)}</select>
              <select name="active" aria-label="Active filter"><option value="">All activation</option><option value="true" selected={query.active === "true"}>Active</option><option value="false" selected={query.active === "false"}>Inactive</option></select><select name="sort" aria-label="Sort targets"><option value="asc">Handle A–Z</option><option value="desc" selected={query.sort === "desc"}>Handle Z–A</option></select>
              <button class="secondary">Apply</button>
            </form>
            {query.q || query.resolution || query.active ? <p><a href="/ops#targets">× Clear filters</a></p> : null}
            <p class="muted">{matchingTargets.length} matching targets · showing {targetPage.length ? offset + 1 : 0}–{Math.min(offset + 25,matchingTargets.length)}</p>
            <form hidden={!targetPage.length} id="bulk-targets" method="post" action="/actions/bulk-activate"><input type="hidden" name="back" value="/ops#targets" /><select name="active" aria-label="Bulk activation"><option value="true">Activate selected</option><option value="false">Deactivate selected</option></select><button class="secondary" disabled>Apply to 0 selected</button></form><p class="muted">Select all on this page{matchingTargets.length>targetPage.length && <> <button type="button" class="secondary" data-select-across={JSON.stringify(matchingTargets.map(t=>t.id))}>Select all {matchingTargets.length} matching targets across pages</button></>}</p>
            {targetPage.length ? (
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th><input type="checkbox" data-select-targets aria-label="Select all targets on this page"/> Identity</th>
                      <th>Resolution</th>
                      <th>Active</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {targetPage.map((t) => (
                      <tr>
                        <td>
                          <input type="checkbox" name="ids" value={t.id} form="bulk-targets" aria-label={`Select ${t.handle ?? t.id}`} />{" "}
                          <span class="platform-glyph" role="img" aria-label={t.platform} title={t.platform}>{t.platform==='fixture'?'♪':t.platform==='spotify'?'◉':t.platform==='billboard'?'▥':'◎'}</span>{' '}
                          <EntityPopover label={<EntityName slug={t.handle ?? t.platform_account_id ?? t.id} displayName={t.display_name ?? (t.handle || t.platform_account_id ? null : 'Target')}/>}
                            summary={`${t.display_name ?? t.handle ?? t.platform_account_id ?? 'Target'} · ${t.platform} · ${t.resolution_status}`}
                            facts={{links:[{label:"Open target",href:`/targets/${t.id}`}],details:[{label:'Role',value:t.role},{label:'Priority',value:t.priority},{label:'Platform',value:t.platform},{label:'Resolution',value:t.resolution_status},{label:'Target set',value:sets.find(set=>set.id===t.target_set_id)?.name},{label:'Active',value:t.activated_at&&!t.deactivated_at?'Yes':'No'}]}}/>
                          {t.platform_account_id && t.platform_account_id!==t.handle ? <div class="target-meta">Account <CopyChip value={t.platform_account_id}/></div> : null}
                        </td>
                        <td>
                          <Badge value={t.resolution_status} /> {health[t.id]&&<TargetHealth health={health[t.id]!}/>}
                        </td>
                        <td>
                          {t.activated_at && !t.deactivated_at ? "Yes" : "No"}
                        </td>
                        <td><div class="row">
                          {t.resolution_status !== "resolved" ? (
                            <form method="post" action="/actions/resolve">
                              <Hidden name="id" value={t.id} />
                              <input
                                aria-label="Platform account ID"
                                name="platform_account_id"
                                required
                                placeholder="Verified platform account ID"
                                value={t.platform_account_id ?? ""}
                              />
                              <button>Resolve</button>
                            </form>
                          ) : (
                            <ButtonForm
                              action="activate"
                              secondary={Boolean(t.activated_at && !t.deactivated_at)}
                              label={
                                t.activated_at && !t.deactivated_at
                                  ? "Deactivate"
                                  : "Activate"
                              }
                              values={{
                                id: t.id,
                                active: String(
                                  !t.activated_at || Boolean(t.deactivated_at),
                                ),
                              }}
                            />
                          )}
                          <a href={`/targets/${t.id}`} aria-label={`Review stale target ${t.handle ?? t.id}`}>Review →</a>
                        </div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div class="empty">
                {targets.length ? <>No targets match these filters. <a href="/ops#targets">Clear filters →</a></> : <EmptyState message="No targets yet" nextStep={{label:"Create or import targets",href:"/ops#target-sets"}}/>}
              </div>
            )}
            <div class="actions compact">{offset ? <a href={`/ops?${new URLSearchParams({...query,target_offset:String(Math.max(0,offset-25))})}#targets`}>← Previous 25 targets</a> : null}
        {offset + 25 < matchingTargets.length ? <a href={`/ops?${new URLSearchParams({...query, target_offset: String(offset + 25)})}#targets`}>Next 25 targets →</a> : null}</div>
          </section>
          <section class="card">
            <h2 id="import-targets">Import targets</h2>{importPreview && <p class="draft-note"><a href="#import-csv" data-resume-draft>Resume draft →</a> · Preserved for 30 minutes</p>}
            <form
              class="stack"
              method="post"
              action="/actions/import"
              enctype="multipart/form-data"
            >
              <label>
                Target set
                <select name="target_set_id" required>
                  {sets.map((s) => (
                    <option value={s.id} selected={importPreview?.target_set_id === s.id}>
                      {s.name} · {s.kind}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                CSV file
                <input type="file" accept=".csv,text/csv" name="file" />
              </label>
              <label>
                Or paste CSV
                <textarea
                  name="csv" id="import-csv"
                  placeholder={
                    "platform,handle,platform_account_id\nfixture,fixture_account,fixture-001"
                  }
                >{importPreview?.csv ?? ""}</textarea>
              </label>
              <label>
                Operation
                <select name="dry_run">
                  <option value="true">Dry run · validate and preview</option>
                  <option value="false">Import these targets</option>
                </select>
              </label>
              <button>Validate / import</button>
            </form>
            <h3 class="section-gap">New target set</h3>
            <form class="stack" method="post" action="/actions/create-set">
              <label>
                Kind
                <input name="kind" required placeholder="account" />
              </label>
              <label>
                Label
                <input name="name" required placeholder="Monitoring accounts" />
              </label>
              <button>Create set</button>
            </form>
            <details class="section-gap" id="target-sets" open><summary>Target sets · {sets.length}</summary>{sets.map(set=><div class="budget-row"><strong>{set.name}</strong><div class="actions">{targets.filter(t=>t.target_set_id===set.id).map(t=>health[t.id]&&<TargetHealth health={health[t.id]!}/>)}</div><p class="muted">{set.kind} · {targets.filter(t=>t.target_set_id===set.id).length} {targets.filter(t=>t.target_set_id===set.id).length===1 ? "target" : "targets"}</p><form method="post" action="/actions/edit-set"><Hidden name="id" value={set.id}/><label>Set label<input name="name" value={set.name} required/></label><button class="secondary">Save label</button></form><form method="post" action="/actions/delete-set" data-confirm="Delete this empty target set? It is removed from the list."><Hidden name="id" value={set.id}/><button class="secondary" disabled={targets.some(t=>t.target_set_id===set.id)}>Delete empty set</button></form></div>)}</details>
          </section>
        </div>
        <div class="grid">
          <section class="card" id="budgets">
            <h2>Budgets</h2>
            <details><summary>Create budget</summary>
              <form class="stack" method="post" action="/actions/create-budget">
                <label>Scope<select name="scope">{["global","tenant","streamline","llm_step","provider"].map(v=><option>{v}</option>)}</select></label>
                <label>Scope ID (empty for global)<input name="scope_id" list="scope-ids" /></label><p class="muted">Global covers all spend. For streamline scope, select its ID below; tenant and LLM step scopes use their registered UUID.</p><datalist id="scope-ids">{streamlines.map(s=><option value={s.id}>{s.source_key}</option>)}</datalist>
                <label>Period<input name="period" required value="monthly" /></label>
                <label>Cap cents<input name="cap_cents" type="number" min="0" required /></label>
                <label>Soft pct<input name="soft_pct" type="number" min="0" max="100" value="80" required /></label>
                <label>Hard action<select name="hard_action">{["warn","pause","degrade"].map(v=><option>{v}</option>)}</select></label>
                <label>Ceiling cents<input name="ceiling_cents" type="number" min="0" required /></label>
                <label>Request cap (provider scope: required, with hard action pause)<input name="cap_requests" type="number" min="0" /></label>
                <button>Create budget</button>
              </form>
            </details>
            {budgets.length ? <div class="table-wrap"><table class="budget-table"><thead><tr><th>Scope</th><th>Window · UTC</th><th>Spend / cap</th><th>Usage</th><th>Approved cap</th></tr></thead><tbody>{budgets.map((b,i)=>{
              const usage=spend.find(v=>v.id===b.id),spent=Number(usage?.spent ?? 0),cap=Number(b.cap_cents);
              const pct=cap>0?spent/cap*100:spent>0?100:0;
              const scope=usage?.scope_name ?? (b.scope_id ? streamlines.find(s=>s.id===b.scope_id)?.source_key ?? null : "Global");
              return <tr data-budget-row hidden={i>=5}>
                <td>{b.scope_id ? <EntityName slug={b.scope_id} displayName={scope ?? 'Unresolved scope'}/> : <strong>{scope}</strong>}<small class="muted">{b.scope==='streamline'?'Function':b.scope==='llm_step'?'LLM step':b.scope}</small></td>
                <td>{b.period}<small class="muted">{usage?.window_start} → {usage?.window_end ?? 'ongoing'}</small></td>
                <td class="budget-spend">{usage?.spent ?? '0'} / {b.cap_cents}¢{b.cap_requests !== null && <small class="muted">cap {b.cap_requests} requests</small>}</td>
                <td><div class={`budget-meter ${spent===0?'zero':pct>=100?'over':pct>=60?'watch':'low'}`} title={`${pct.toFixed(0)}% used · Soft threshold ${b.soft_pct}% · Cap ${b.cap_cents}¢ · Ceiling ${b.ceiling_cents ?? 'unset'}¢`}><progress max={cap||1} value={spent} aria-label={`${scope ?? 'Unresolved scope'}: ${pct.toFixed(0)}% of cap used`}/><span class="soft-tick" style={`left:${b.soft_pct}%`}/></div><small class="muted">{pct.toFixed(0)}% · warn at {b.soft_pct}%</small></td>
                <td><details><summary>Adjust approved cap</summary><p>Budget <CopyChip value={b.id} label={b.id.slice(0,8)}/></p><p class="muted">Ceiling {b.ceiling_cents ?? 'unset'}¢</p><form method="post" action="/actions/budget"><Hidden name="id" value={b.id}/><label>New cap (cents)<input name="cap_cents" type="number" min={b.cap_cents} max={b.ceiling_cents ?? undefined} required/></label><button class="secondary">Raise cap</button></form></details></td>
              </tr>;
            })}</tbody></table></div> : <EmptyState message="No budgets yet" nextStep={{label:"Create a budget",href:"/ops#budgets"}}/>}
            {budgets.length>5 && <button class="secondary compact" type="button" data-all-budgets>Show all {budgets.length}</button>}
          </section>
          <section class="card">
            <h2>Runner & dbt</h2>
            <p>
              Active runner: <Badge value={runner.runner} />
            </p>
            <ButtonForm
              action="runner"
              label={`Switch to ${runner.runner === "core" ? "cloud" : "core"}`}
              values={{ runner: runner.runner === "core" ? "cloud" : "core" }}
            />
            <form class="compact" method="post" action="/actions/dbt-trigger">
              <label>
                Registered job ID
                <input name="job_id" required />
              </label>
              <button>Trigger job</button>
            </form>
            <p class="muted">
              Runner changes take effect on the next cycle. Cloud actions need a
              configured dbt Cloud token.
            </p>
          </section>
          <section class="card" id="recovery">
            <h2>Recovery</h2>
            <datalist id="recovery-dumps">{recovery.dumps.map(d=><option value={d.id}>{`Output file · ${d.source_key ?? 'run '+d.run_id.slice(0,8)} · ${relTime(d.created_at)}`}</option>)}</datalist>
            <datalist id="recovery-cycles">{recovery.cycles.map(cycle=><option value={cycle.id}>{`${cycle.cadence} · ${cycle.scope === "global" ? "global" : "tenant"} · closed ${relTime(cycle.closed_at)}`}</option>)}</datalist>
            <datalist id="recovery-global-cycles">{recovery.cycles.filter(cycle=>cycle.scope==="global").map(cycle=><option value={cycle.id}>{`${cycle.cadence} · closed ${relTime(cycle.closed_at)}`}</option>)}</datalist>
            <datalist id="recovery-warehouses">{recovery.warehouses.map(w=><option value={w.id}>{`${w.database} · ${w.adapter}${w.is_production?' · production':''}`}</option>)}</datalist>
            {!recovery.dumps.length && <EmptyState message="No recent output files available." nextStep={{label:"Run a function",href:"/functions"}}/>}
            {!recovery.cycles.length && <EmptyState message="No closed cycles available." nextStep={{label:"Open cycle recovery",href:"/runbooks/cycle-not-closed"}}/>}
            {!recovery.warehouses.length && <EmptyState message="No warehouses registered." nextStep={{label:"Set up the local stack",command:"bash ops/local/up.sh"}}/>}
            <form class="stack" method="post" action="/actions/repair" data-confirm="Repair this landing? Retained output is loaded again.">
              <label>
                Dump ID
                <input name="dump_id" list="recovery-dumps" required />
              </label>
              <label>
                Declared output table
                <input
                  name="target_table"
                  required
                  placeholder="raw.chart_entries"
                />
              </label>
              <button class="secondary">Repair landing</button>
            </form>
            <form class="compact" method="post" action="/actions/backfill" data-confirm="Request a backfill? The selected inputs run again.">
              <label>
                Function
                <select name="source_key">
                  {streamlines.map((s) => (
                    <option>{s.source_key}</option>
                  ))}
                </select>
              </label>
              <label>Closed cycle ID<input name="cycle_id" list="recovery-cycles" required /></label>
              <button class="secondary">Request backfill</button>
            </form>
            <form class="compact" method="post" action="/actions/retry" data-confirm="Retry this cycle? Its cadence and scope run again through Core.">
              <label>Cycle ID<input name="cycle_id" list="recovery-cycles" required /></label>
              <button class="secondary">Retry cycle</button>
            </form>
            <form class="compact" method="post" action="/actions/replay" data-confirm="Replay this closed global cycle? The current build is restored afterward.">
              <label>Closed global cycle ID<input name="cycle_id" list="recovery-global-cycles" required /></label>
              <button class="secondary">Replay cycle</button>
            </form>
            <p class="muted">Tenant Replay is unavailable; use Retry to rebuild the tenant’s current cycle.</p>
            <form class="compact" method="post" action="/actions/migrate" data-confirm="Migrate retained output? A copy is loaded into this warehouse.">
              <label>Destination warehouse ID<input name="warehouse_id" list="recovery-warehouses" required /></label>
              <button class="secondary">Migrate retained output</button>
            </form>
          </section>
        </div>
        <section class="card section-gap" id="alerts"><div class={`row ${!visibleAlerts.length?"empty-heading":""}`}><h2>{query.resolved === "true" ? "Resolved alerts" : query.acknowledged === "true" ? "Acknowledged alerts" : openAlerts.length===0 ? "No open alerts" : visibleAlerts.length===0 ? "No alerts match this view" : "Open alerts"}{visibleAlerts.length>0&&<> · <span class={query.resolved==="true" || query.acknowledged==="true"?"count-muted":alertTone(visibleAlerts)}>{visibleAlerts.length}</span></>}</h2><a href={query.resolved === "true" ? "/ops#alerts" : "/ops?resolved=true#alerts"}>{query.resolved === "true" ? "Open alerts" : "Resolved history"} →</a></div>
        {[...alertGroups].map(([key,group])=><div class="alert-group"><div class="row"><div><h3><ChangedDot at={group[0]!.opened_at}/>{humanize(group[0]!.class)}{group.length>1?` × ${group.length}`:''} · {group[0]!.source_key ? <a href={`/functions/${group[0]!.source_key}`}>{humanize(group[0]!.source_key)} →</a> : "Platform"}</h3><p class="muted"><Badge value={group.some(a=>a.severity==='critical') ? 'critical' : group[0]!.severity}/> · Latest <Value value={group[0]!.opened_at}/></p></div>{group[0]!.class==="schema_drift"&&group[0]!.source_key?(driftPages.get(group[0]!.source_key)??[]).slice(-1).map(d=><DriftPopover changes={d.changes} fingerprint={d.fingerprint} source={group[0]!.source_key!} acknowledged={group.every(a=>!!a.acknowledged_by)}/>):null}{query.resolved!=="true"&&<ButtonForm action="ack-alert-group" label={group.every(a=>a.acknowledged_by) ? "All acknowledged" : `Acknowledge ${group.filter(a=>!a.acknowledged_by).length} remaining`} secondary disabled={group.every(a=>a.acknowledged_by)} values={{ids:group.filter(a=>!a.acknowledged_by).map(a=>a.id).join(','),alert_class:group[0]!.class,source_key:group[0]!.source_key ?? "Platform"}}/>}</div><details><summary>Inspect {group.length} {group.length===1?'alert':'alerts'}</summary>{[...group.reduce((groups,a)=>{const key=JSON.stringify([a.message,a.subject_type==="target"?a.subject_id:null]);const existing=groups.get(key);if(existing)existing.push(a);else groups.set(key,[a]);return groups;},new Map<string,typeof group>())].map(([,repeats],i)=>{const a=repeats[0]!, target=targets.find(t=>t.id===a.subject_id);return <div class="alert-detail" data-alert-row hidden={i>=10}><strong>{a.message}</strong><RefusalPopover code={a.class} message={a.message}/>{a.class==="schema_drift"&&a.source_key?(driftPages.get(a.source_key)??[]).slice(-1).map(d=><DriftPopover changes={d.changes} fingerprint={d.fingerprint} source={a.source_key!} acknowledged={!!a.acknowledged_by}/>):null}<p class="muted">{a.subject_type==="target"?<EntityName slug={a.subject_id} displayName={target?.display_name ?? target?.handle ?? target?.platform_account_id ?? 'Target'} href={`/ops?q=${encodeURIComponent(target?.handle ?? target?.platform_account_id ?? a.subject_id)}#targets`}/>:"Run-scoped alert · latest occurrence below"} · {repeats.length} occurrence{repeats.length===1?'':'s'} · <RelativeTime at={a.opened_at}/> · {repeats.filter(item=>!item.acknowledged_by).length} unacknowledged</p><div class="actions">{a.run_id && <a href={`/runs/${a.run_id}`}>Producing run →</a>}{a.runbook_slug && <a href={`/runbooks/${a.runbook_slug}`}>Runbook →</a>}<ButtonForm action="resolve-alert" label="Resolve latest" secondary values={{id:a.id}}/></div></div>})}<button type="button" class="secondary" data-show-alerts>Show all alert groups</button></details></div>)}
        {!alertGroups.size ? <EmptyState message={query.resolved!=="true" && !openAlerts.length ? "No open alerts" : "No alerts match this view."} nextStep={{label:"View alert history",href:"/ops?resolved=true#alerts"}}/> : null}</section>

      </>
    ),
  });
}
export { functionView } from "./function-view.js";
export async function weeklyPage(context: Context) {
  const c = createRouterClient(router, { context });
  const [data,alerts,prior] = await Promise.all([c.screen.weekly({}),attention(context.db),rows(context.db,z.object({n:z.string()}),"SELECT count(*)::text AS n FROM control.alert WHERE acknowledged_by IS NULL AND opened_at<=now()-interval '7 days' AND (resolved_at IS NULL OR resolved_at>now()-interval '7 days')")]);
  const acknowledgedAlerts = await attention(context.db, false, true);
  const openAlerts=alerts.filter(a=>!a.acknowledged_by);
  const severity=[...new Set(openAlerts.map(a=>a.severity))].map(severity=>`${openAlerts.filter(a=>a.severity===severity).length} ${severity}`).join(' · ');
  const alertDelta=openAlerts.length-Number(prior[0]?.n ?? 0);
  return Layout({ title: "Weekly overview", subtitle: "Last seven days of delivery",
    children: <div class="grid poster">
      <section class="card hero"><div class="eyebrow">01 / Delivery</div><h2>Rows landed</h2><div class="metric">{data.rows_landed}</div><p class="muted">{data.rows_landed === "0" ? "No deliveries yet" : "Rows delivered in the last seven days"}</p><details><summary>By day and source</summary><Table nextStep={{label:"View functions",href:"/functions"}} rows={data.rows_by_day_source}/></details></section>
      <section class="card"><div class="eyebrow">02 / Runs</div><h2>Delivery state</h2>{data.runs_by_status.length ? data.runs_by_status.map(r=><a class="row delivery-link" href={`/runs?status=${encodeURIComponent(String(r.status))}`}><Badge value={r.status}/><strong class={Number(r.count)===0?"count-muted":r.status==="failed"?"count-danger":["partial","draining"].includes(String(r.status))?"count-warn":undefined}>{r.count} →</strong></a>) : <p class="muted">No runs yet</p>}<a href="/functions">View functions →</a></section>
      <section class="card freshness"><div class="eyebrow">03 / Freshness</div><h2>Cadence health</h2><div class="cadence-strip">{data.freshness_by_cadence.map(f=><div><strong>{f.cadence}</strong> <Badge value={!f.last_at ? "Awaiting first run" : f.stale ? "stale" : "active"}/><p class="muted">{f.last_at ? <Value value={f.last_at}/> : <a href={`/functions?cadence=${f.cadence}`}>{f.cadence} functions →</a>}</p></div>)}</div></section>
      <p class="muted"><a href="/ops?acknowledged=true#alerts">{acknowledgedAlerts.length} acknowledged alerts await recovery →</a></p>
      {[{title:"Open alerts",value:String(openAlerts.length),anchor:"alerts",zero:"No open alerts"},
        {title:"Stale targets",value:data.stale_targets,anchor:"targets",zero:"No stale targets"},
        {title:"Pending drift",value:data.drift_pending,anchor:"alerts",filter:"schema_drift",zero:"No pending drift"}].map((v,i)=><section class="card"><div class="eyebrow">0{i+4} / Attention</div><h2>{v.title}</h2><div class={`metric ${Number(v.value)===0?"count-muted":v.title==="Open alerts" && openAlerts.some(a=>a.severity==="critical")?"count-danger":"count-warn"}`}>{v.value}</div><p class="muted">{v.title === "Open alerts" ? `${severity || 'No open alerts'} · ${alertDelta >= 0 ? '+' : ''}${alertDelta} vs last week` : v.value === "0" ? v.zero : "Needs review"}</p><a href={`/ops${"filter" in v ? `?alert_class=${v.filter}` : ""}#${v.anchor}`}>Review {v.title.toLowerCase()} →</a></section>)}
      <section class="card freshness"><div class="row"><div><div class="eyebrow">07 / Spend</div><h2>Cost by scope</h2></div><a href="/ops#budgets">Manage budgets →</a></div>{data.cost_by_scope.some(c=>c.cost_cents !== "0") ? <div class="table-wrap"><table><thead><tr><th>Scope</th><th>Spend</th></tr></thead><tbody>{data.cost_by_scope.filter(c=>c.cost_cents!=="0").map(c=><tr><td>{c.scope_id ? <EntityName slug={c.scope_id} displayName={c.scope_name}/> : <strong>{c.scope_name ?? "Global"}</strong>}<small class="muted"> {c.scope_kind==="streamline"?"Function":c.scope_kind==="llm_step"?"LLM step":c.scope_kind==="tenant"?"Tenant":""}</small></td><td>{c.cost_cents}¢</td></tr>)}</tbody></table></div> : <p class="muted">No metered spend recorded this week.</p>}</section>
    </div> });
}
export async function action(
  context: Context,
  name: string,
  f: Record<string, string>,
) {
  const c = createRouterClient(router, { context });
  const val = (key: string) => f[key] ?? "";
  switch (name) {
    case "create-tenant":
      return c.tenants.create({ slug: val("slug"), name: z.string().trim().min(1).parse(val("name")) });
    case "patch-tenant":
      return c.tenants.patch({ id: val("id"), name: z.string().trim().min(1).parse(val("name")), status: z.enum(["active", "inactive"]).parse(val("status")) });
    case "probe":
      return c.streamlines.dryProbe({ source_key: val("source_key"), scope: val("scope") || "global" });
    case "run":
      return c.streamlines.runNow({
        source_key: val("source_key"),
        key: val("key"),
        scope: val("scope") || "global",
        ...(val("fixture_scenario") ? { fixture_scenario: z.enum(["normal","html","missing_stats","not_found","retry","all_null"]).parse(val("fixture_scenario")) } : {}),
      });
    case "pause":
      return c.streamlines.patchKnobs({
        source_key: val("source_key"),
        enabled: val("enabled") === "true",
      });
    case "unpark":
      return c.streamlines.unpark({ source_key: val("source_key") });
    case "edit-set": {
      const updated=await rows(context.db,jsonRow,"UPDATE control.target_set SET name=$2,updated_at=now() WHERE id=$1 RETURNING id",[z.uuid().parse(val('id')),z.string().trim().min(1).max(200).parse(val('name'))]);
      return {updated:updated.length};
    }
    case "delete-set": {
      const deleted=await rows(context.db,jsonRow,"DELETE FROM control.target_set s WHERE id=$1 AND NOT EXISTS (SELECT 1 FROM control.target t WHERE t.target_set_id=s.id) RETURNING id",[z.uuid().parse(val('id'))]);
      if (!deleted.length) throw new Error('Only empty target sets can be deleted.');
      return {deleted:deleted.length};
    }
    case "ack-alert-group": {
      const ids=z.array(z.uuid()).min(1).max(10000).parse(val('ids').split(','));
      for (const id of ids) await c.alerts.acknowledge({id});
      return {acknowledged:ids.length,alert_class:val("alert_class"),source_key:val("source_key")};
    }
    case "create-set":
      return c.targets.createSet({
        kind: val("kind"),
        name: val("name"),
        tenant_id: null,
      });
    case "import":
      return c.targets.importTargets({
        target_set_id: val("target_set_id"),
        csv: val("csv"),
        dry_run: val("dry_run") !== "false",
      });
    case "undo-import":
      return c.targets.undoImport({audit_id:val("audit_id")});
    case "resolve":
      return c.targets.resolve({
        id: val("id"),
        platform_account_id: val("platform_account_id"),
      });
    case "activate":
      return c.targets.bulkActivate({
        ids: [val("id")],
        active: val("active") === "true",
      });
    case "review":
      return c.targets.patch({ id: val("id"), handle: val("handle"), display_name: val("display_name"), active: val("active") === "true" });
    case "bulk-activate":
      return c.targets.bulkActivate({ ids: val("ids").split(",").filter(Boolean), active: val("active") === "true" });
    case "create-budget":
      return c.budgets.create({ scope: z.enum(["global","tenant","streamline","llm_step","provider"]).parse(val("scope")), scope_id: val("scope_id") || null,
        period: val("period"), cap_cents: val("cap_cents"), soft_pct: Number(val("soft_pct")), hard_action: z.enum(["warn","pause","degrade"]).parse(val("hard_action")), ceiling_cents: val("ceiling_cents"),
        cap_requests: val("cap_requests") || null });
    case "budget":
      return c.budgets.raise({ id: val("id"), cap_cents: val("cap_cents") });
    case "runner":
      return c.dbt.runnerMode.set({
        runner: z.enum(["core", "cloud"]).parse(val("runner")),
      });
    case "dbt-trigger":
      return c.dbt.jobs.trigger({ job_id: val("job_id") });
    case "repair":
      return c.streamlines.repair({
        dump_id: val("dump_id"),
        target_table: val("target_table"),
      });
    case "backfill":
      return c.streamlines.backfill({ source_key: val("source_key"), cycle_id: val("cycle_id") });
    case "migrate":
      return c.streamlines.migrate({ warehouse_id: val("warehouse_id") });
    case "reimport":
      return c.reference.reimport({ source: val("source") });
    case "retry":
      return c.dbt.retry({ cycle_id: val("cycle_id") });
    case "replay":
      return c.dbt.replay({ cycle_id: val("cycle_id") });
    case "ack-alert":
      return c.alerts.acknowledge({ id: val("id") });
    case "resolve-alert":
      return c.alerts.resolve({ id: val("id") });
    case "drift":
      return c.streamlines.acknowledgeDrift({
        source_key: val("source_key"),
        fingerprint: val("fingerprint"),
      });
    case "reset-cursor": {
      const cursor = val("cursor_choice") ? z.object({target_id:z.uuid().nullable(),cursor_key:z.string()}).parse(JSON.parse(val("cursor_choice"))) : { target_id: val("target_id") || null, cursor_key: val("cursor_key") };
      return c.streamlines.resetCursor({source_key:val("source_key"),...cursor});
    }
    default:
      throw new Error("Unknown action");
  }
}

export async function platformPage(context: Context, query: Record<string, string>) {
  const client = createRouterClient(router, { context });
  const since = query.since ?? new Date(Date.now() - 7 * 86400000).toISOString();
  const [holdings, events, sources] = await Promise.all([
    client.platform.holdings({ since }),
    client.platform.events({ after: query.after, limit: 100 }),
    client.platform.sources({}).catch(() => null),
  ]);
  const nextStep = { label: "Open runs", href: "/runs" };
  return Layout({ title: "Holdings and events", subtitle: "Collected rows and stored rows measure different things.", children: <>
    <form method="get" action="/ops/platform"><label>Since (UTC)<input name="since" value={since}/></label><button>Read holdings</button></form>
    <section class="card"><h2>Sources</h2>{sources ? <Table rows={sources.sources} nextStep={{ label: "Open functions", href: "/functions" }} empty="No enabled sources or recent scheduled loads."/> : <p>Sources are unavailable.</p>}<a href="/functions">See each source's runs</a></section>
    <section class="card"><h2>Collected rows</h2><Table rows={holdings.ingestion.rows} nextStep={nextStep} empty="No scheduled loads in this window."/></section>
    <section class="card"><h2>Inventory history</h2><p>History is unavailable before {holdings.inventory_history.first_snapshot_day ?? "the first snapshot"}.</p>
      <Table rows={holdings.inventory_history.days.map(day => ({ day: day.day, state: day.state, layers: day.layers }))} nextStep={{ label: "Check snapshots", href: "/runbooks/showcase-inventory-failed" }} empty="No snapshots in this window."/></section>
    <section class="card"><h2>Vendor usage</h2><p>{holdings.vendor_cost.cost_cents} cents · {holdings.vendor_cost.label}. Infrastructure cost is excluded.</p><a href="/ops">Inspect usage</a></section>
    <section class="card"><h2>Events</h2><p>Runner: {events.runner.state}. {events.runner.next_step}</p><p>Each poll includes a two-minute overlap. Repeated keys describe the same event. Open runs for details.</p><Table rows={events.events} nextStep={nextStep} empty="No events in this window."/>
      <a href={`/ops/platform?since=${encodeURIComponent(since)}&after=${encodeURIComponent(events.next_cursor)}`}>Read next events</a></section>
    <a href="/ops">Open operations</a>
  </> });
}
