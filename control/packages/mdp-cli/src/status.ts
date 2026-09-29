import type { PlatformStatus } from "@mdp/contracts";
export function age(seconds: number | undefined) {
  if (seconds === undefined) return "unknown";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}
export function formatStatus(status: PlatformStatus) {
  const table = [
    ["CADENCE / SCOPE", "CLOSE", "AGE", "STATE", "SHA / IMAGE", "OPEN (AGE)"],
    ...status.cadences.map(row => [
      `${row.cadence} / ${row.scope}`, row.last_closed?.close_no ?? "none", age(row.last_closed?.age_seconds),
      !row.last_closed ? "no close" : row.overdue ? "overdue" : "current",
      `${row.last_closed?.git_sha?.slice(0, 12) ?? "unknown"} / ${row.last_closed?.image_digest ?? "unknown"}`,
      row.open.map(c => `${c.id.slice(0, 8)} (${age(c.age_seconds)})`).join(", ") || "none",
    ]),
  ];
  const widths = table[0]!.map((_, i) => Math.max(...table.map(row => row[i]!.length)));
  return [
    `${status.verdict.toUpperCase()} · ${status.checked_at}`,
    ...table.map(row => row.map((value, i) => value.padEnd(widths[i]!)).join("  ").trimEnd()),
    ...status.alerts.map(a => `${a.severity} ${a.class}: ${a.count} · ${[...a.runbook_urls, ...(a.no_guide ? ["no guide"] : [])].join(", ")}`),
    `Alert email / 24 h: ${status.sends.failed} failed attempts · ${status.sends.pending} pending · ${status.sends.skipped} skipped · ${status.sends.gave_up} abandoned`,
    `Heartbeat: ${status.heartbeat ? `${status.heartbeat.configured ? status.heartbeat.action : "not configured"} · ${status.heartbeat.cadence} · ${age(status.heartbeat.age_seconds)} ago` : "not recorded"}. Set MDP_HEARTBEAT_URL; see /runbooks/service-unreachable#external-heartbeat`,
    `Retry launcher: ${status.retry_launcher.state}`,
    status.retry_launcher.message,
    `Builds: ${status.versions.state} · control ${status.versions.control_sha ?? "unknown"} · data ${status.versions.data_sha ?? "unknown"}`,
    ...status.reasons.map(reason => `- ${reason}`),
  ].join("\n");
}
