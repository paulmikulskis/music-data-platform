import { errorHint } from "@mdp/contracts";
import { ORPCError } from "@orpc/client";
import postgres from "postgres";
import { isRecord } from "./unknown";

export function readTimeoutError() {
  const hint = errorHint("platform_read_timeout");
  return new ORPCError("SERVICE", {
    defined: true,
    status: 503,
    message: `${hint.summary} ${hint.next_step}`,
    data: {
      error_class: "platform_read_timeout",
      message: hint.summary,
      next_step: hint.next_step,
      runbook: hint.runbook ? `/runbooks/${hint.runbook}` : null,
    },
  });
}

export function readTimeoutResponse(request: Request, error: unknown) {
  const databaseTimeout =
    error instanceof postgres.PostgresError &&
    ["55P03", "57014"].includes(error.code);
  const upstreamTimeout =
    error instanceof ORPCError &&
    error.status === 503 &&
    error.code === "SERVICE" &&
    isRecord(error.data) &&
    error.data.error_class === "platform_read_timeout";
  if (!databaseTimeout && !upstreamTimeout) return null;

  const failure = readTimeoutError();
  const options = {
    status: failure.status,
    headers: { "Cache-Control": "no-store", "Retry-After": "1" },
  };
  if (request.method === "HEAD") return new Response(null, options);
  const path = new URL(request.url).pathname;
  if (path.startsWith("/rpc/"))
    return Response.json({ json: failure.toJSON() }, options);
  if (path.startsWith("/api/")) return Response.json(failure.toJSON(), options);
  return new Response(failure.message, options);
}
