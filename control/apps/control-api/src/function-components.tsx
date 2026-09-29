import type { Child } from "hono/jsx";
import type { z } from "zod";
import { functionPage, runDto } from "@mdp/contracts";
import type { FunctionHealth, TargetState } from "./console-data.js";
import type { Labels, Verdict } from "./console-semantics.js";
import { leadColumns } from "./page-data.js";
import { RelativeTime, relTime } from "./primitives.js";

export const targetWords: Record<TargetState, string> = {
  read: "Read",
  unchanged: "Unchanged",
  failed: "Failed",
  gone: "Gone",
  parked: "Parked",
  "not-read": "Not read yet",
};
export function FunctionVerdict({ verdict }: { verdict: Verdict }) {
  const tone =
    verdict.label === "Healthy"
      ? "active"
      : verdict.label === "Failing"
        ? "failed"
        : "warning";
  return <span class={`badge ${tone}`}>{verdict.label}</span>;
}
export function RightsChip({ labels }: { labels: Labels }) {
  const learning =
    labels.learning_eligible == null
      ? "learning unknown"
      : labels.learning_eligible
        ? "learning allowed"
        : "no learning";
  const resale =
    labels.resale_permitted == null
      ? "resale unknown"
      : labels.resale_permitted
        ? "resale allowed"
        : "no resale";
  return (
    <a
      class="badge rights-chip"
      href="/ops#rights"
      data-hover="Rights annotate rows. They never block reading."
      data-next-href="/ops#rights"
      data-next-label="Rights registry →"
    >
      Rights: {learning}, {resale} ⓘ
    </a>
  );
}
export function FlowLine({
  reads,
  writes,
}: {
  reads: string[];
  writes: string[];
}) {
  return (
    <p class="flow-line">
      Reads{" "}
      <span data-density-value>
        {reads.length ? reads.join(", ") : "not measured yet"}
      </span>{" "}
      <span aria-hidden="true">→</span> lands in{" "}
      {writes.length ? (
        writes.map((w) => (
          <a data-density-value href={`/explorer?q=${encodeURIComponent(w)}`}>
            {w.replace(/^raw\./, "")}
          </a>
        ))
      ) : (
        <a href="/runs">run records</a>
      )}
    </p>
  );
}
export function TargetStrip({
  health,
  source,
}: {
  health: FunctionHealth;
  source: string;
}) {
  return (
    <section class="card target-card">
      <div class="row">
        <h2>Targets</h2>
        <a
          href={`/functions/${source}?open=targets#targets`}
          data-strip-filter="all"
        >
          All {health.total} →
        </a>
      </div>
      <div class="strip-legend">
        {Object.entries(health.counts)
          .filter(([, n]) => n > 0)
          .map(([state, n]) => (
            <button class="secondary" data-strip-filter={state} data-count={n}>
              {n} {state === "not-read" ? "not read yet" : state}
            </button>
          ))}
      </div>
      <div id="target-strip" role="grid" aria-label="Target health">
        {health.rows.map((t, i) => (
          <a
            class={`cell s-${t.state}`}
            role="gridcell"
            tabindex={i === 0 ? 0 : -1}
            href={`/targets/${t.id}`}
            data-platform={t.platform}
            data-state={t.state}
            data-hover={`${t.name.slice(0, 100)} · ${targetWords[t.state]} · ${t.at ? relTime(t.at) : "not measured yet"}`}
            data-next-href={`/targets/${t.id}`}
            data-next-label="Open target →"
            aria-label={`${t.name} · ${targetWords[t.state]}`}
          />
        ))}
      </div>
      {health.total ? (
        <p class="muted target-caption">
          {health.total} of {health.same_platform} {health.platform} targets in{" "}
          {health.set_name}
        </p>
      ) : (
        <p class="muted">
          No run targets yet.{" "}
          <a href={`/functions/${source}/logs`}>Open logs →</a>
        </p>
      )}
    </section>
  );
}
export function RunStrip({
  runs,
  source,
  week = [],
}: {
  runs: z.infer<typeof runDto>[];
  source: string;
  week?: z.infer<typeof runDto>[];
}) {
  const chart = (items: typeof runs) => {
    const max = Math.max(1, ...items.map((r) => Number(r.rows_written)));
    return (
      <div class="run-bars" role="grid" aria-label="Rows per run">
        {[...items].reverse().map((r, i) => (
          <a
            role="gridcell"
            tabindex={i === 0 ? 0 : -1}
            href={`/runs/${r.id}`}
            data-run-id={r.id}
            data-status={r.status}
            data-landed={r.rows_written}
            data-rejected={r.rows_rejected}
            class={`run-bar ${Number(r.rows_written) === 0 ? "zero" : r.status === "failed" ? "failed" : r.coverage === "partial" ? "partial" : "succeeded"}`}
            style={`--bar-height:${Math.max(3, (Number(r.rows_written) * 100) / max)}px`}
            data-hover={`Run ${r.id.slice(0, 8)} · ${r.status} · ${Number(r.rows_written).toLocaleString("en")} rows · ${relTime(r.created_at)}`}
            data-next-href={`/runs/${r.id}`}
            data-next-label="Open run →"
            aria-label={`Run ${r.id.slice(0, 8)} · ${r.status}`}
          />
        ))}
      </div>
    );
  };
  return (
    <section
      class="card"
      id="recent-runs"
      data-live-url={`/functions/${source}/part/live`}
      data-live-run="true"
    >
      <div id="run-chart">
        <div class="row">
          <h2>Runs</h2>
          <label class="run-window">
            <span class="sr-only">Window</span>
            <select data-spark-window>
              <option value="20">20</option>
              <option value="7d">7d</option>
            </select>
          </label>
        </div>
        <div data-spark-range="20">{chart(runs)}</div>
        <div data-spark-range="7d" hidden>
          {chart(week)}
        </div>
        {runs.length ? (
          <p class="muted run-summary">
            {
              runs.filter(
                (r) => r.status === "succeeded" && r.coverage !== "partial",
              ).length
            }{" "}
            succeeded ·{" "}
            {
              runs.filter(
                (r) => r.coverage === "partial" && r.status !== "failed",
              ).length
            }{" "}
            partial · {runs.filter((r) => r.status === "failed").length} failed
          </p>
        ) : (
          <p>
            No runs yet. <a href={`/functions/${source}/logs`}>Open logs →</a>
          </p>
        )}
        <div class="actions">
          <a href={`/runs?source_key=${source}`}>All runs →</a>
          <a href={`/functions/${source}/logs`}>Logs →</a>
          <span role="status" class="sr-only">
            Live
          </span>
        </div>
      </div>
    </section>
  );
}
export function LazyDisclosure({
  source,
  part,
  label,
  children,
  open = false,
}: {
  source: string;
  part: string;
  label: string;
  children?: Child;
  open?: boolean;
}) {
  return (
    <details
      class="utility-disclosure card"
      id={part}
      data-lazy={`/functions/${source}/part/${part}`}
      data-part={part}
      data-loaded={open ? "true" : undefined}
      open={open}
    >
      <summary>{label}</summary>
      <div data-lazy-body>
        {children ?? (
          <a href={`/functions/${source}?open=${part}#${part}`}>
            Open {part} →
          </a>
        )}
      </div>
    </details>
  );
}
function Cell({ value }: { value: unknown }) {
  if (value == null) return <span class="muted">not measured yet</span>;
  if (typeof value === "object")
    return <span>{`{…} ${Object.keys(value).length} keys`}</span>;
  if (typeof value === "string" && /^\d{4}-\d\d-\d\dT/.test(value))
    return <RelativeTime at={value} />;
  return (
    <>
      {typeof value === "number"
        ? Intl.NumberFormat("en", { notation: "compact" }).format(value)
        : String(value).slice(0, 1000)}
    </>
  );
}
export function LatestRows({
  preview,
  source,
  run,
  total,
}: {
  preview: z.infer<typeof functionPage>["output_preview"][number];
  source: string;
  run?: z.infer<typeof runDto>;
  total?: string;
}) {
  const lead = leadColumns(preview.columns);
  const columns = preview.columns.map((c) => c.name);
  return (
    <div data-preview data-column-key={preview.table}>
      <div class="row">
        <p class="muted">
          {run ? (
            <>
              Read <RelativeTime at={run.created_at} /> · run{" "}
              <span data-density-value>{run.id.slice(0, 8)}</span> ·{" "}
            </>
          ) : null}
          {preview.rows.length} of {total ?? "not measured yet"} rows
        </p>
        <details class="columns-choice">
          <summary data-column-summary>
            Columns {lead.length} of {columns.length} ▾
          </summary>
          {columns.map((c) => (
            <label>
              <span>
                <input
                  type="checkbox"
                  data-column-name={c}
                  checked={lead.includes(c)}
                />{" "}
                {c}
              </span>
            </label>
          ))}
        </details>
      </div>
      {preview.rows.length ? (
        <>
          <div class="latest-table">
            <table>
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th data-col={c} hidden={!lead.includes(c)}>
                      <button class="secondary" data-sort={c}>
                        {c}
                      </button>
                    </th>
                  ))}
                  <th>
                    <span class="sr-only">Open row</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row, i) => (
                  <tr>
                    {columns.map((c) => (
                      <td
                        data-col={c}
                        hidden={!lead.includes(c)}
                        data-sort-value={
                          typeof row[c] === "object" ? "" : String(row[c] ?? "")
                        }
                      >
                        <Cell value={row[c]} />
                      </td>
                    ))}
                    <td>
                      <button
                        class="secondary"
                        data-row-open={`row-${i}`}
                        aria-label={`Open row ${i + 1}`}
                      >
                        ↗
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div class="latest-mobile">
            {preview.rows.map((row, i) => (
              <button class="row-card secondary" data-row-open={`row-${i}`}>
                <strong>
                  <Cell value={row[lead[0] ?? ""]} />
                </strong>
                <span>
                  {lead.slice(1, 4).map((c) => (
                    <span>
                      <small>{c.replaceAll("_", " ")}</small>
                      <Cell value={row[c]} />
                    </span>
                  ))}
                </span>
              </button>
            ))}
          </div>
          {preview.rows.map((row, i) => (
            <dialog class="row-sheet" id={`row-${i}`}>
              <form method="dialog">
                <button class="secondary">Close</button>
              </form>
              <h2>Row {i + 1}</h2>
              <dl>
                {[...new Set([...columns, ...Object.keys(row)])].map((c) => (
                  <>
                    <dt>{c}</dt>
                    <dd>
                      {(c === "_run_id" || c === "_target_id") &&
                      typeof row[c] === "string" ? (
                        <a
                          href={`/${c === "_run_id" ? "runs" : "targets"}/${encodeURIComponent(row[c])}`}
                        >
                          {row[c]}
                        </a>
                      ) : typeof row[c] === "object" && row[c] !== null ? (
                        <details>
                          <summary>
                            <Cell value={row[c]} />
                          </summary>
                          <pre>
                            {JSON.stringify(row[c], null, 2).slice(0, 4000)}
                          </pre>
                        </details>
                      ) : (
                        <Cell value={row[c]} />
                      )}
                    </dd>
                  </>
                ))}
              </dl>
              <a
                href={`/functions/${source}/preview.csv?table=${encodeURIComponent(preview.table)}`}
              >
                CSV ↓
              </a>
            </dialog>
          ))}
        </>
      ) : (
        <p>
          No rows yet. <a href={`/functions/${source}/logs`}>Open logs →</a>
        </p>
      )}
      <div class="actions rows-links">
        <a href={`/explorer?q=${encodeURIComponent(preview.table)}`}>
          Explorer →
        </a>
        <a
          href={`/workbench?relation=${encodeURIComponent(preview.table.replace(/^raw\./, "explore_raw."))}`}
        >
          Workbench →
        </a>
        <a href={`/explorer?q=${encodeURIComponent(preview.table)}`}>
          Raw table →
        </a>
        {run && <a href={`/runs/${run.id}`}>Run {run.id.slice(0, 8)} →</a>}
        <a href={`/functions/${source}/logs${run ? `?run=${run.id}` : ""}`}>
          Logs →
        </a>
        <a
          href={`/functions/${source}/preview.csv?table=${encodeURIComponent(preview.table)}`}
          download
        >
          CSV ↓
        </a>
      </div>
    </div>
  );
}
export function ProblemsCard({ children }: { children: Child }) {
  return (
    <section class="card problems-card" id="problems">
      <h2>Needs a look</h2>
      {children}
    </section>
  );
}
