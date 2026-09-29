import { z } from "zod";
import { empty, get } from "./shared.js";

export const cycleStatusDto = z.object({
  id: z.uuid(), close_no: z.string().nullable(), closed_at: z.iso.datetime({ offset: true }).nullable(),
  opened_at: z.iso.datetime({ offset: true }), age_seconds: z.number(), git_sha: z.string().nullable(),
  image_digest: z.string().nullable(),
});
export const cadenceStatusDto = z.object({
  cadence: z.enum(["hourly", "daily", "weekly"]), scope: z.string(),
  interval_seconds: z.number(), overdue: z.boolean(),
  last_closed: cycleStatusDto.nullable(), open: z.array(cycleStatusDto),
});
export const alertGroupDto = z.object({
  class: z.string(), severity: z.enum(["info", "warning", "critical"]), count: z.string(),
  runbook_urls: z.array(z.string()), no_guide: z.boolean(),
});
export const sendsStatusDto = z.object({ failed: z.string(), pending: z.string(), skipped: z.string(), gave_up: z.string() });
export const versionDto = z.object({ git_sha: z.string().nullable() });
export const versionsStatusDto = z.object({
  control_sha: z.string().nullable(), data_sha: z.string().nullable(),
  state: z.enum(["match", "mismatch", "unknown", "unavailable"]),
});
export const heartbeatStatusDto = z.object({
  action: z.enum(["heartbeat.sent", "heartbeat.skipped", "heartbeat.failed"]),
  at: z.iso.datetime({ offset: true }),
  age_seconds: z.number(),
  cadence: z.enum(["hourly", "daily", "weekly"]),
  configured: z.boolean(),
});
// Public monitoring carries no source, tenant, run, key or build identifiers.
export const healthSummaryDto = z.object({
  checked_at: z.iso.datetime({ offset: true }),
  ok: z.boolean(),
  overdue: z.array(z.enum(["hourly", "daily", "weekly"])),
  next_step: z.literal("Open /ops and follow the runner recovery guide."),
});
export const platformStatusDto = z.object({
  retry_launcher: z.object({ state: z.enum(["configured", "missing"]), message: z.string() }),
  checked_at: z.iso.datetime({ offset: true }), verdict: z.enum(["healthy", "attention", "broken"]),
  reasons: z.array(z.string()), rules: z.record(z.string(), z.string()),
  cadences: z.array(cadenceStatusDto), alerts: z.array(alertGroupDto),
  heartbeat: heartbeatStatusDto.nullable(),
  sends: sendsStatusDto, versions: versionsStatusDto,
});
export type PlatformStatus = z.infer<typeof platformStatusDto>;

export const statusContract = get("/status", empty, platformStatusDto);
