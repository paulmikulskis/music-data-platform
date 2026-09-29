import nodemailer from "nodemailer";
import { z } from "zod";
import { errorHint } from "@mdp/contracts";
import { database, rows, required } from "./db.js";
export const emailClasses = [
  "schema_breaking",
  "llm_budget_exceeded",
  "cost_cap_hit",
  "control_db_unavailable",
  "object_store_unavailable",
  "warehouse_unavailable",
  "litellm_unavailable",
  "dbt_api_unavailable",
];
export function shouldEmail(severity: string, alertClass: string) {
  return (
    severity === "critical" ||
    (severity === "warning" &&
      (emailClasses.includes(alertClass) ||
        alertClass.endsWith("_unavailable")))
  );
}
// A failed delivery retries after 30 s, doubling up to 1 h; the tenth failure gives up.
export const emailRetry = {
  baseSeconds: 30,
  capSeconds: 3600,
  maxAttempts: 10,
};
export function retryDelaySeconds(attempt: number) {
  return Math.min(
    emailRetry.baseSeconds * 2 ** (attempt - 1),
    emailRetry.capSeconds,
  );
}
export function emailTransport() {
  const provider = process.env.RESEND_API_KEY
    ? "resend"
    : process.env.SMTP_URL
      ? "smtp"
      : null;
  if (provider) {
    return process.env.MDP_EMAIL_FROM?.trim() &&
      process.env.MDP_EMAIL_TO?.trim()
      ? provider
      : null;
  }
  if (process.env.MDP_AUTH_MODE === "dev") return "console";
  return null;
}
// Open, unacknowledged alerts that email and have been neither sent nor given up.
const unsent = `a.resolved_at IS NULL AND a.acknowledged_by IS NULL
  AND (a.severity='critical' OR (a.severity='warning' AND (a.class=ANY($1::text[]) OR a.class LIKE '%\\_unavailable')))
  AND NOT EXISTS (SELECT 1 FROM control.audit_log l WHERE l.action IN ('email.sent','email.gave_up') AND l.subject=a.id::text)`;
// The latest numbered failure holds the attempt count and its retry delay. Failure rows without
// an attempt number come from the earlier unbounded retry loop and do not count.
const due = `SELECT a.id,a.class,a.runbook_slug,coalesce(f.attempt,0)::int AS attempts FROM control.alert a
  LEFT JOIN LATERAL (SELECT (l.after->>'attempt')::int AS attempt,l.at,(l.after->>'retry_after_s')::int AS retry_after_s
    FROM control.audit_log l WHERE l.action='email.failed' AND l.subject=a.id::text AND l.after->>'attempt' IS NOT NULL
    ORDER BY (l.after->>'attempt')::int DESC LIMIT 1) f ON true
  WHERE ${unsent} AND (f.attempt IS NULL OR f.at + f.retry_after_s * interval '1 second' <= now())`;
export async function drainAlerts(db = database()) {
  const transport = emailTransport();
  if (!transport) return skipAlerts(db);
  const pending = await rows(
    db,
    z.object({ id: z.string() }),
    `${due} ORDER BY a.opened_at LIMIT 20`,
    [emailClasses],
  );
  for (const candidate of pending) {
    await db.begin(async (tx) => {
      const locked =
        await tx`SELECT pg_try_advisory_xact_lock(hashtext(${`mdp-email:${candidate.id}`})) AS locked`;
      if (!locked[0]?.locked) return;
      const found = await rows(
        tx,
        z.object({
          id: z.string(),
          class: z.string(),
          runbook_slug: z.string().nullable(),
          attempts: z.number(),
        }),
        `${due} AND a.id=$2`,
        [emailClasses, candidate.id],
      );
      const alert = found[0];
      if (!alert) return;
      const attempt = alert.attempts + 1;
      try {
        const url = new URL(
          `/runbooks/${alert.runbook_slug ?? alert.class.replaceAll("_", "-")}`,
          process.env.MDP_PUBLIC_URL ?? "http://127.0.0.1:8090",
        ).toString();
        const subject = `MDP alert: ${alert.class}`;
        const text = `${alert.class}\nAlert: ${alert.id}\nRunbook: ${url}`;
        if (transport === "resend") {
          const response = await fetch("https://api.resend.com/emails", {
            method: "POST",
            headers: {
              authorization: `Bearer ${process.env.RESEND_API_KEY}`,
              "content-type": "application/json",
              "idempotency-key": `mdp-alert-${alert.id}`,
            },
            body: JSON.stringify({
              from: required("MDP_EMAIL_FROM"),
              to: [required("MDP_EMAIL_TO")],
              subject,
              text,
            }),
            signal: AbortSignal.timeout(15000),
          });
          if (!response.ok) throw new Error("Email delivery failed");
        } else if (transport === "smtp") {
          await nodemailer.createTransport(process.env.SMTP_URL).sendMail({
            from: required("MDP_EMAIL_FROM"),
            to: required("MDP_EMAIL_TO"),
            subject,
            text,
            messageId: `<mdp-${alert.id}@alerts.invalid>`,
          });
        } else
          console.log(
            JSON.stringify({ event: "alert_email", transport, subject, text }),
          );
        await tx.unsafe(
          "INSERT INTO control.audit_log(actor,action,subject,after) VALUES ('email-worker','email.sent',$1,$2::text::jsonb)",
          [
            alert.id,
            JSON.stringify({
              state: "sent",
              transport,
              attempt,
              runbook_url: url,
            }),
          ],
        );
      } catch {
        const exhausted = attempt >= emailRetry.maxAttempts;
        await tx.unsafe(
          "INSERT INTO control.audit_log(actor,action,subject,after) VALUES ('email-worker','email.failed',$1,$2::text::jsonb)",
          [
            alert.id,
            JSON.stringify(
              exhausted
                ? {
                    state: "gave_up",
                    transport,
                    attempt,
                    error_class: "email_delivery_failed",
                  }
                : {
                    state: "retry_pending",
                    transport,
                    attempt,
                    retry_after_s: retryDelaySeconds(attempt),
                    error_class: "email_delivery_failed",
                  },
            ),
          ],
        );
        if (!exhausted) return;
        await tx.unsafe(
          "INSERT INTO control.audit_log(actor,action,subject,after) VALUES ('email-worker','email.gave_up',$1,$2::text::jsonb)",
          [
            alert.id,
            JSON.stringify({
              state: "gave_up",
              transport,
              attempts: attempt,
              error_class: "email_delivery_failed",
            }),
          ],
        );
        console.warn(
          JSON.stringify({
            event: "alert_email_gave_up",
            alert_id: alert.id,
            class: alert.class,
            transport,
            attempts: attempt,
          }),
        );
      }
    });
  }
}
// Incomplete setup makes no attempt. The skipped row survives restarts and does not block a later send.
async function skipAlerts(db: ReturnType<typeof database>) {
  await db.begin(async (tx) => {
    const locked =
      await tx`SELECT pg_try_advisory_xact_lock(hashtext('mdp-email:skip')) AS locked`;
    if (!locked[0]?.locked) return;
    await tx.unsafe(
      `INSERT INTO control.audit_log(actor,action,subject,after)
        SELECT 'email-worker','email.skipped',a.id::text,$2::text::jsonb
        FROM control.alert a WHERE ${unsent}
          AND NOT EXISTS (SELECT 1 FROM control.audit_log l WHERE l.action='email.skipped' AND l.subject=a.id::text)`,
      [
        emailClasses,
        JSON.stringify({
          state: "skipped",
          reason: "not_configured",
          error_class: "email_delivery_unconfigured",
          ...errorHint("email_delivery_unconfigured"),
        }),
      ],
    );
  });
}

export function startEmailWorker(drain = drainAlerts) {
  if (!emailTransport())
    console.warn(
      JSON.stringify({
        event: "alert_email_disabled",
        reason: "not_configured",
        ...errorHint("email_delivery_unconfigured"),
      }),
    );
  const tick = () =>
    void drain().catch(() =>
      console.error(
        "Alert email check failed. Open /ops and retry after the database recovers.",
      ),
    );
  const timer = setInterval(tick, 10000);
  timer.unref();
  tick();
  return () => clearInterval(timer);
}
