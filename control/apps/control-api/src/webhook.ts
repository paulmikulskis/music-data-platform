import { createHmac, timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { AppError } from "./db.js";
export function verifyWebhook(
  body: string,
  signature: string | null,
  secret: string | undefined,
) {
  if (!secret) {
    if (process.env.MDP_AUTH_MODE === "dev") return;
    throw new AppError("unauthorized", "Webhook secret is not configured", 401);
  }
  const expected = createHmac("sha256", secret).update(body).digest("hex");
  const actual = (signature ?? "").replace(/^sha256=/, "");
  if (
    !/^[0-9a-f]{64}$/.test(actual) ||
    !timingSafeEqual(Buffer.from(expected), Buffer.from(actual))
  )
    throw new AppError("unauthorized", "Invalid webhook signature", 401);
}
export function webhookPayload(body: unknown) {
  const event = z
    .object({
      eventId: z.string(),
      eventType: z.string(),
      data: z.object({
        runId: z.union([z.string(), z.number()]),
        jobId: z.union([z.string(), z.number()]),
        runStatus: z.string().optional(),
        runStatusCode: z.number().optional(),
        runStatusMessage: z.string().optional(),
      }),
    })
    .parse(body);
  const failed =
    event.data.runStatusCode === 20 ||
    /error|fail|cancel/i.test(event.data.runStatus ?? event.eventType);
  const success =
    event.data.runStatusCode === 10 ||
    /success|complete/i.test(event.data.runStatus ?? event.eventType);
  const status = z
    .enum(["running", "succeeded", "failed"])
    .parse(failed ? "failed" : success ? "succeeded" : "running");
  return {
    event_id: event.eventId,
    run_id: String(event.data.runId),
    job_id: String(event.data.jobId),
    status,
    message: event.data.runStatusMessage ?? event.eventType,
  };
}
