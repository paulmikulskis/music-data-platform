import { CopySQL } from "./copy-sql";
// Matches the data API build envelope. Kept structural so the app can precede that SDK release.
export type Build = {
  relation: string;
  scope: "global" | "tenant";
  tenant_slug: string | null;
  stamped: boolean;
  cycle_id: string | null;
  close_no: string | null;
  built_at: string | null;
};
// A place on the small map: a Shazam city or a market.
export type Place = { name: string; lat: number; lon: number };
// plain is the hover sentence; sources are source keys; links open public pages; places draw the map.
export type Provenance = {
  queried_at: string;
  scope: string;
  cadence?: string;
  close_no?: string | null;
  build?: Build;
  inputs?: Build[];
  observed_at?: string | null;
  window?: string;
  // A live query reads now; a reviewed list is the platform's own reviewed register, such as the
  // rights register, so its count has a known source without a build.
  provenance?: "live query" | "reviewed list";
  query: string;
  sql: string;
  copyKind?: "sql" | "command";
  plain?: string;
  sources?: string[];
  links?: { label: string; href: string }[];
  places?: Place[];
  // The sheet's one way on. Without it, the sheet opens the entries behind the number.
  next?: { label: string; href: string };
};
export function provenanceKnown(p?: Provenance): boolean {
  return (
    !!p?.queried_at &&
    (p.provenance === "live query" ||
      p.provenance === "reviewed list" ||
      !!p.build?.stamped ||
      (!!p.inputs?.length && p.inputs.every((i) => i.stamped)))
  );
}
export function Number({
  value,
  label,
  provenance: p,
  percentage,
  compact = false,
}: {
  value: string | null;
  label: string;
  provenance?: Provenance;
  percentage?: { count: number; n: number };
  compact?: boolean;
}) {
  const known = provenanceKnown(p);
  const shown = percentage
    ? percentage.n < 20
      ? `${percentage.count} of ${percentage.n}`
      : `${((percentage.count / percentage.n) * 100).toFixed(1)}%`
    : value;
  if (compact)
    return (
      <span
        className="number compact"
        data-provenance={known ? "known" : "unknown"}
      >
        <span className="value">{shown ?? "—"}</span>
        <span className="metric-label">{label}</span>
        {!known && <small>Source unknown</small>}
      </span>
    );
  const build = p?.build;
  const scope = build
    ? build.scope === "tenant"
      ? `tenant:${build.tenant_slug}`
      : "global"
    : p?.scope;
  const close = build?.close_no ?? p?.close_no;
  return (
    <section className="number">
      <p className="metric-label">{label}</p>
      <div className="value">{shown ?? "—"}</div>
      {percentage && percentage.n >= 20 && <p>n = {percentage.n}</p>}
      <p className="citation">
        {p?.cadence}
        {close ? ` #${close}` : ""}
        {scope ? ` · ${scope}` : ""}
        {!known
          ? " · provenance unknown"
          : p?.provenance
            ? ` · ${p.provenance}`
            : ""}
      </p>
      {p?.window && <p className="citation">{p.window}</p>}
      <details>
        <summary>
          View evidence <span aria-hidden="true">↗</span>
        </summary>
        {p ? (
          <div className="evidence">
            <p>{p.query}</p>
            <dl>
              <dt>Queried</dt>
              <dd>{p.queried_at}</dd>
              {p.observed_at && (
                <>
                  <dt>Observed</dt>
                  <dd>{p.observed_at}</dd>
                </>
              )}
            </dl>
            {[...(p.build ? [p.build] : []), ...(p.inputs ?? [])].map(
              (input) => (
                <p key={input.relation}>
                  {input.relation} ·{" "}
                  {input.scope === "tenant"
                    ? `tenant:${input.tenant_slug}`
                    : "global"}{" "}
                  ·{" "}
                  {input.stamped
                    ? `#${input.close_no ?? "unknown"} · built ${input.built_at ?? "unknown"}`
                    : "provenance unknown"}
                </p>
              ),
            )}
            <CopySQL sql={p.sql} kind={p.copyKind} />
            <pre>{p.sql}</pre>
          </div>
        ) : (
          <p>Provenance is unknown. Reload to try the query again.</p>
        )}
      </details>
    </section>
  );
}
