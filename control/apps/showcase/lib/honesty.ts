import { readerFreshness } from "@mdp/contracts/health-policy.generated";
import type { Source } from "../components/sources";

export type HonestyState =
  | "current"
  | "empty"
  | "partial"
  | "running"
  | "queued"
  | "overdue"
  | "unavailable"
  | "paused"
  | "off"
  | "tenant"
  | "access"
  | "previous"
  | "planned"
  | "unknown";
export type Honesty = {
  state: HonestyState;
  label: string;
  at: string | null;
  checkedAt: string | null;
};

// Missing evidence never becomes a successful read. Each date keeps its own meaning.
export function honesty(source: Source | null, now = Date.now()): Honesty {
  const evidence = source?.evidence;
  const result = (
    state: HonestyState,
    label: string,
    at: string | null = null,
  ): Honesty => ({
    state,
    label,
    at,
    checkedAt: evidence?.checked_at ?? null,
  });
  if (!source) return result("unavailable", "State unavailable · Retry");
  if (!evidence)
    return result("unknown", "State not measured yet · see details");
  if (evidence.incident?.class === "provider_credentials_missing")
    return result(
      "access",
      "Waiting for provider access",
      evidence.incident.at,
    );
  if (evidence.tenant_bound && !source.last_read && !source.enabled)
    return result("tenant", "Runs per tenant", evidence.configured_at);
  if (!source.enabled) {
    if (evidence.incident)
      return result("paused", "Paused · see details", evidence.incident.at);
    return result("off", "Built · switched off", evidence.configured_at);
  }
  if (source.tracked?.count === 0)
    return result("empty", "Enabled · no targets", source.tracked.as_of);
  const attempt = evidence.attempt;
  if (attempt?.status === "running" || attempt?.status === "queued")
    return result(
      attempt.status,
      attempt.status === "running" ? "Reading now" : "Queued",
      attempt.at,
    );
  if (attempt?.status === "partial")
    return result("partial", "Only part delivered · see details", attempt.at);
  if (!evidence.last_success)
    return result(
      "empty",
      "Enabled · no successful readings",
      evidence.configured_at,
    );
  // Weekly playlist targets rotate through UTC weekday buckets on a daily reader.
  // The recorded reader cadence, not its target cadence or name, sets this window.
  const cadence = source.cadence;
  if (cadence !== "hourly" && cadence !== "daily" && cadence !== "weekly")
    return result("unknown", "Schedule not measured yet · see details");
  const policy = readerFreshness[cadence];
  const windowMs = (policy.period_seconds + policy.grace_seconds) * 1000;
  if (
    attempt?.status === "failed" ||
    Date.parse(evidence.last_success) + windowMs < now
  )
    return result("overdue", "Overdue · see details", evidence.last_success);
  return result("current", "Up to date", evidence.last_success);
}

// Incident details are admitted by the API only for this source and current attempt.
// No generic alert or registry default supplies a promise of repair.
export function remediation(source: Source) {
  return source.evidence?.incident?.remediation ?? null;
}
