import { z } from "zod";
import { selectSchemas as s } from "@mdp/control-db";
import { bigintWire } from "@mdp/data-sdk";
import { empty, get, post } from "./shared.js";
import { alertDto } from "./budgets-alerts.js";

// design: the four reference alert classes, each on subject_type reference_source.
export const referenceAlertClasses = [
  "reference_generation_incomplete",
  "reference_disk_high",
  "reference_dump_stale",
  "reference_import_failed",
] as const;

const nullableTime = z.iso.datetime().nullable();

const tableCounts = z.record(z.string(), z.number()).nullable();

export const referenceSourceDto = s.reference_source
  .pick({
    source: true,
    imported_generation: true,
    import_generation: true,
    import_phase: true,
    import_message: true,
    landed_generation: true,
    landed_reconciled: true,
    probe_error: true,
    reimport_requested_by: true,
  })
  .extend({
    import_state: z.enum(["running", "validated", "promoted", "failed"]).nullable(),
    export_date: nullableTime,
    replication_sequence: bigintWire.nullable(),
    imported_at: nullableTime,
    import_started_at: nullableTime,
    import_finished_at: nullableTime,
    landed_at: nullableTime,
    mirror_counts: tableCounts,
    landed_counts: tableCounts,
    disk_used_bytes: bigintWire.nullable(),
    disk_total_bytes: bigintWire.nullable(),
    // The closure trigger, from the newest mb_spine landing; absent before one measures it.
    closure_write_share: z.number().nullable().default(null),
    closure_recordings: bigintWire.nullable().default(null),
    closure_measured_at: nullableTime.default(null),
    probed_at: nullableTime,
    reimport_requested_at: nullableTime,
    updated_at: z.iso.datetime(),
  });

export const referenceSourceState = referenceSourceDto.extend({
  alerts: z.array(alertDto),
});

export const referenceContract = {
    list: get("/reference", empty, z.array(referenceSourceState)),
    reimport: post(
      "/reference/reimport",
      z.object({ source: z.string().regex(/^[a-z][a-z0-9_]*$/) }),
      referenceSourceDto,
    ),
  };
