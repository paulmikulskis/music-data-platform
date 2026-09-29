import { z } from "zod";
import sandboxPolicy from "./sandbox-policy.json" with { type: "json" };
import { errorHint } from "@mdp/contracts";
import { labelsFor } from "@mdp/data-sdk";
// Small view models: backends can supply these without changing the UI primitives.
export type Coverage = {
  succeeded: number;
  total: number;
  floor?: number;
  failures: { id: string; name: string; reason: string }[];
};
export type Health = {
  id: string;
  name: string;
  active: boolean;
  resolved: boolean;
  parked?: boolean;
  zero_yield_run?: string;
  result?: string;
  at?: string;
  http_status?: number;
  missing_streak?: number;
  probe_action?: string;
};
export type Failure = {
  title: string;
  message: string;
  model?: string;
  cycle_id?: string;
  run_id?: string;
  runbook: string;
  source_key?: string;
  error_class?: string;
  at?: string;
  retryable?: boolean;
  note?: string;
};
export type Labels = {
  layer?: string | undefined;
  category?: string;
  tenant?: string;
  learning_eligible?: boolean;
  resale_permitted?: boolean;
  rights_status?: string;
  derived_from?: string[];
  per_row?: boolean;
};
export type Drift = {
  name: string;
  before?: string;
  after?: string;
  own_code?: boolean;
};
export const docs = "/runbooks/forbidden-path";
export function healthState(h: Health) {
  if (h.parked) return "parked";
  if (
    (h.missing_streak ?? 0) >= 2 &&
    [404, 410].includes(h.http_status ?? 0) &&
    (!h.result || /404|410/.test(h.result))
  )
    return "dead";
  if (!h.at) return "not read yet";
  if (h.http_status === 304 && h.result === "unchanged") return "unchanged";
  if (h.result !== "succeeded") return "stale";
  return "healthy";
}
export function relationLabels(
  name: string,
  tags: string[] = [],
  layer?: string | null,
): Labels {
  if (name.startsWith(sandboxPolicy.prefix))
    return {
      layer: "sandbox",
      tenant: "unknown",
      category: "personal",
      learning_eligible: false,
      resale_permitted: false,
      derived_from: [],
    };
  if (name.includes(".")) return warehouseLabels(name);
  const schema = name.split(".")[0] ?? "";
  return {
    layer:
      layer ??
      tags.find((t) => t.startsWith("layer:"))?.slice(6) ??
      tags.find((t) => ["bronze", "silver", "gold", "universal"].includes(t)) ??
      (/^(raw|staging)(\.|$)|^stg_/.test(name)
        ? "bronze"
          : /^(intermediate|marts|tenant_.*_(marts|intermediate))(\.|$)|^(int_|mart_)/.test(
                name,
              )
            ? "silver"
            : undefined),
    tenant:
      schema.match(/^tenant_(.+)_(?:marts|staging|intermediate)$/)?.[1] ??
      (tags.includes("scope:tenant") ? "tenant scoped" : "global"),
  };
}
// Presentation names match the existing console chips; values have one generated source.
export function warehouseLabels(name: string, schema?: string | null): Labels {
  const parts = name.split(".");
  const label = labelsFor(
    schema ?? (parts.length > 1 ? parts[0]! : "unknown"),
    parts.at(-1)!,
  );
  return {
    layer: label.layer,
    category: label.category,
    tenant: label.tenant,
    learning_eligible: label.learning,
    resale_permitted: label.resale,
    rights_status: label.licence_status,
  };
}
export function schemaChanges(
  before: Record<string, unknown>,
  after: Record<string, unknown>,
  own: string[] = [],
): Drift[] {
  return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]))
    .map((name) => ({
      name,
      ...(before[name] !== undefined ? { before: String(before[name]) } : {}),
      ...(after[name] !== undefined ? { after: String(after[name]) } : {}),
      own_code: own.includes(name),
    }));
}
export function refusalInfo(code: string, message = "") {
  const hint = errorHint(code);
  return {
    label: code.replaceAll("_", " ") || "unknown error",
    why: hint.summary,
    path: hint.next_step,
    message: message || hint.summary,
    runbook: hint.runbook ? `/runbooks/${hint.runbook}` : null,
  };
}
export function driftHistory(history: Record<string, unknown>[]) {
  const previous = new Map<string, Record<string, unknown>>();
  return history
    .filter((h) => h.table !== "raw._rejected")
    .map((h) => {
      const columns =
        typeof h.columns === "object" && h.columns && !Array.isArray(h.columns)
          ? z.record(z.string(), z.unknown()).parse(h.columns)
          : {};
      const table = String(h.table ?? "unknown");
      const before = previous.get(table);
      previous.set(table, columns);
      return {
        table,
        fingerprint: String(h.fingerprint ?? ""),
        changes: before
          ? schemaChanges(
              before,
              columns,
              Array.isArray(h.expected_columns)
                ? h.expected_columns.map(String)
                : [],
            )
          : [],
      };
    });
}

export function writerScope(tenantBound: boolean[]): string | undefined {
  if (!tenantBound.some(Boolean)) return undefined;
  return tenantBound.every(Boolean)
    ? "tenant scoped"
    : "mixed global and tenant";
}

export function sandboxFacts(row: Record<string, unknown>) {
  const size = Number(row.size_bytes ?? 0),
    quota = Number(row.quota_bytes ?? 0);
  return {
    size:
      size < 1048576
        ? `${(size / 1024).toFixed(0)} KiB`
        : `${(size / 1048576).toFixed(1)} MiB`,
    quota: `${(quota / 1048576).toFixed(0)} MiB`,
    state:
      row.login_enabled === false
        ? "disabled"
        : row.manual_frozen || row.quota_frozen
          ? "frozen"
          : "ready",
    percent: quota ? Math.round((size / quota) * 100) : 0,
  };
}

export type Verdict = {
  label:
    | "Running"
    | "Paused"
    | "Failing"
    | "Needs a look"
    | "Not run yet"
    | "Healthy";
  action: string | null;
  href?: string;
  command?: "pause" | "probe" | "unpark";
  runbook?: string;
};
export function functionVerdict(
  s: { enabled: boolean; source_key: string; writes: string[] },
  lastRun:
    { status: string; id: string; error_class?: string | null } | undefined,
  coverage: Coverage | undefined,
  parked: number,
  drift: number,
  alerts: { class: string }[],
): Verdict {
  if (lastRun && ["queued", "running", "draining"].includes(lastRun.status))
    return { label: "Running", action: null };
  if (!s.enabled)
    return { label: "Paused", action: "Resume", command: "pause" };
  if (lastRun?.status === "failed") {
    const hint = errorHint(lastRun.error_class ?? "unmapped");
    return {
      label: "Failing",
      action: "Retry run",
      href: `/runs/${lastRun.id}`,
      runbook: `/runbooks/${hint.runbook ?? "cadence-failed"}`,
    };
  }
  if (
    coverage &&
    coverage.total > 0 &&
    coverage.succeeded / coverage.total < (coverage.floor ?? 1)
  )
    return {
      label: "Needs a look",
      action: `Review ${coverage.failures.length} failed targets`,
      href: `?open=targets&state=failed#targets`,
    };
  if (parked)
    return {
      label: "Needs a look",
      action: `Release ${parked} parked inputs`,
      command: "unpark",
    };
  if (drift || alerts.some((a) => a.class === "schema_drift"))
    return {
      label: "Needs a look",
      action: "Review schema change",
      href: "?open=schema#schema",
    };
  if (!lastRun)
    return { label: "Not run yet", action: "Probe", command: "probe" };
  const first = s.writes[0];
  return {
    label: "Healthy",
    action: "See rows",
    href: first
      ? `/workbench?relation=${encodeURIComponent(first.replace(/^raw\./, "explore_raw."))}`
      : `/functions/${s.source_key}/logs`,
  };
}
export function rejectLabel(code: string): string {
  const labels: Record<string, string> = {
    "unsupported_item:episode": "Podcast episode, not a track",
    "unsupported_item:musicVideo": "Music video, not a track",
    unsupported_item: "Unsupported item type",
    not_a_trackplay: "Not a track play",
    envelope_mismatch: "Response shape changed",
    validation_error: "Record did not match its schema",
    missing_required: "Required field missing",
    input_rejected: "Input could not be read",
    stale_target: "Target unavailable",
  };
  const text = labels[code] ?? labels[code.split(":")[0] ?? ""];
  if (text) return text;
  const words = code.replaceAll("_", " ").replaceAll(":", " · ");
  return words ? words[0]!.toUpperCase() + words.slice(1) : "Record rejected";
}
