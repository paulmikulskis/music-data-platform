import "server-only";
import { z } from "zod";
import { isRecord } from "./unknown";
import { readTimeoutError, readTimeoutResponse } from "./read-timeout";
import { createHash } from "node:crypto";
import { origin, required, type Session } from "@mdp/showcase-auth";
import { verifyProof } from "./proof-token";
import { viewerPath } from "../lib/proof-link";
import { budget, type ReadBudget } from "./read-budget";
import {
  consoleOperation,
  consoleMutationAllowed,
  consolePath,
  injectBackBar,
  locationFor,
  upstreamHeaders,
} from "./console-policy";
import { ConsoleCookies } from "./console-cookies";
import {
  consoleOperations,
  operationPending,
  type ConsolePayload,
} from "./console-operations";
const platformTimeout = z.object({
  code: z.literal("SERVICE"),
  status: z.literal(503),
  data: z.object({ error_class: z.literal("platform_read_timeout") }),
});
const rpcPlatformTimeout = z.object({ json: platformTimeout });
async function boundedBody(request: Request) {
  if (Number(request.headers.get("content-length") ?? 0) > 1100000) return null;
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const parts: Uint8Array[] = [];
  let size = 0;
  let expired = false;
  const timer = setTimeout(() => {
    expired = true;
    void reader.cancel();
  }, 10000);
  try {
    while (true) {
      const chunk = await reader.read();
      if (expired) return null;
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > 1100000) {
        await reader.cancel();
        return null;
      }
      parts.push(chunk.value);
    }
    return Buffer.concat(parts);
  } finally {
    clearTimeout(timer);
    reader.releaseLock();
  }
}
export function createConsoleProxy(
  gate: ReadBudget = budget,
  fetcher: typeof fetch = (...args) => fetch(...args),
  timeoutMs = 35000,
  operations = consoleOperations,
) {
  const cookies = new ConsoleCookies();
  const cache = gate.cache<ConsolePayload>();
  return async function proxy(
    request: Request,
    current: Session,
  ): Promise<Response> {
    const path = new URL(request.url);
    if (!consolePath(path.pathname))
      return new Response("Page unavailable. Open /today.", { status: 404 });
    if (!consoleMutationAllowed(request, origin()))
      return new Response(
        "This action could not be checked. Update your browser, then open /ops and try again.",
        { status: 403 },
      );
    const upstream = required("MDP_CONTROL_API_URL");
    const proofToken = path.searchParams.get("showcase_proof");
    const context = verifyProof(proofToken, current.handle);
    if (proofToken && !context)
      return Response.redirect(new URL("/live", origin()), 303);
    path.searchParams.delete("showcase_proof");
    const { weight, read } = consoleOperation(path.pathname, request.method);
    const prefix = `console:${current.person.api_key_id}:${current.id_hash}:`;
    const headersIn = upstreamHeaders(
      request,
      current.person.admin_key,
      path.pathname.startsWith("/workbench")
        ? cookies.get(current.id_hash)
        : undefined,
    );
    const bodyIn = ["GET", "HEAD"].includes(request.method)
      ? undefined
      : await boundedBody(request);
    if (bodyIn === null)
      return new Response(
        "The upload is too large or too slow. Use a smaller file and retry.",
        { status: 413 },
      );
    const work = async (): Promise<ConsolePayload> => {
      // No client signal or response deadline cancels this fetch. Admission follows the complete body.
      const response = await fetcher(
        new URL(path.pathname + path.search, upstream),
        {
          method: request.method,
          headers: headersIn,
          body: bodyIn,
          redirect: "manual",
          cache: "no-store",
        },
      );
      cookies.save(current.id_hash, response.headers.getSetCookie());
      if (response.status === 401 || response.status === 403) {
        await response.arrayBuffer();
        throw Object.assign(new Error("Open /sign-in."), {
          status: response.status,
        });
      }
      const headers = new Headers({
        "Cache-Control": "no-store",
        "Referrer-Policy": "same-origin",
      });
      for (const name of [
        "content-type",
        "content-disposition",
        "etag",
        "retry-after",
        "x-correlation-id",
      ]) {
        const value = response.headers.get(name);
        if (value) headers.set(name, value);
      }
      const location = response.headers.get("location");
      if (location) {
        const safe = locationFor(location, upstream, origin());
        if (safe) {
          const destination = new URL(safe);
          if (context && proofToken)
            destination.searchParams.set("showcase_proof", proofToken);
          headers.set("location", destination.href);
        } else {
          await response.arrayBuffer();
          return {
            status: 502,
            headers: [...headers],
            body: new TextEncoder().encode(
              "That destination is unavailable. Open /ops.",
            ).buffer,
          };
        }
      }
      const body = await response.arrayBuffer();
      if (response.status >= 500) {
        if (response.status === 503) {
          const json: unknown = await new Response(body)
            .json()
            .catch(() => null);
          const schema = path.pathname.startsWith("/rpc/")
            ? rpcPlatformTimeout
            : platformTimeout;
          if (schema.safeParse(json).success) throw readTimeoutError();
        }
        throw new Error("The console is unavailable. Open /status.");
      }
      return { status: response.status, headers: [...headers], body };
    };
    const admitted = async () => {
      if (read) {
        const input = createHash("sha256")
          .update(bodyIn ?? "")
          .digest("hex");
        const key =
          prefix +
          (context ? "proof:" : "operator:") +
          request.method +
          ":" +
          path.pathname +
          path.search +
          ":" +
          input +
          ":" +
          JSON.stringify(
            ["accept", "accept-language", "content-type", "range"].map((h) =>
              request.headers.get(h),
            ),
          );
        const result = await cache.read(
          key,
          weight,
          work,
          30000,
          current.id_hash,
        );
        return decorate(
          result.value,
          context,
          result.state,
          context ? proofToken : null,
        );
      }
      // Writes keep the actor's key and invalidate that session's cached console pages.
      const result = await gate.run(
        weight,
        async () => {
          if (weight === "heavy" && gate.runnerBusy)
            throw new Error("Collection is active or unknown. Open /status.");
          gate.invalidate(prefix);
          return work();
        },
        current.id_hash,
      );
      gate.invalidate(prefix);
      return decorate(result, context, "live");
    };
    try {
      const { id, promise } = operations.start(current.id_hash, async () => {
        try {
          return await admitted();
        } catch (error) {
          const status = isRecord(error) ? error.status : undefined;
          if (status !== 401 && status !== 403) throw error;
          return {
            status: 303,
            headers: [
              [
                "location",
                new URL("/sign-in?reason=key-refused", origin()).href,
              ],
              ["cache-control", "no-store"],
            ],
            body: new ArrayBuffer(0),
          };
        }
      });
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const result = await Promise.race([
          promise,
          new Promise<null>((resolve) => {
            timer = setTimeout(() => resolve(null), timeoutMs);
          }),
        ]);
        return result
          ? payloadResponse(result, request.method)
          : operationPending(id);
      } finally {
        clearTimeout(timer);
      }
    } catch (error) {
      const retry = readTimeoutResponse(request, error);
      if (retry && path.pathname !== "/workbench") return retry;
      if (context) {
        return payloadResponse(
          decorate(
            {
              status: 503,
              headers: [
                ["Content-Type", "text/html; charset=utf-8"],
                ["Cache-Control", "no-store"],
              ],
              body: new TextEncoder().encode(
                path.pathname === "/workbench"
                  ? workbenchUnavailable(path.searchParams.get("sql"))
                  : '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main><h1>Source details are unavailable.</h1><p>Check collection status or go back to the showcase.</p><a href="/live">Open status</a></main></body></html>',
              ).buffer,
            },
            context,
            "live",
            proofToken,
          ),
          request.method,
        );
      }
      return new Response(
        weight === "heavy" && gate.runnerBusy
          ? "Collection is active or its state is unknown. Open /status to check progress."
          : "The request could not be completed. Open /status to check progress.",
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }
  };
}
function decorate(
  payload: ConsolePayload,
  context: ReturnType<typeof verifyProof>,
  state: string,
  token: string | null = null,
): ConsolePayload {
  const headers = new Headers(payload.headers);
  headers.set("X-Showcase-Read", state);
  if (!headers.get("content-type")?.includes("text/html"))
    return { ...payload, headers: [...headers] };
  const note = context
    ? "You're in the operator console"
    : state === "live"
      ? ""
      : "Saved details · return to the showcase to refresh.";
  const back = new URL(
    viewerPath(context?.back) ??
      (context?.song ? `/s/song/${encodeURIComponent(context.song)}` : "/"),
    origin(),
  ).toString();
  return {
    ...payload,
    headers: [...headers],
    body: new TextEncoder().encode(
      injectBackBar(
        new TextDecoder().decode(payload.body),
        back,
        note,
        token ?? undefined,
      ),
    ).buffer,
  };
}
export function payloadResponse(payload: ConsolePayload, method = "GET") {
  return new Response(
    [204, 304].includes(payload.status) || method === "HEAD"
      ? null
      : payload.body,
    { status: payload.status, headers: payload.headers },
  );
}
export const proxyConsole = createConsoleProxy();

export function workbenchUnavailable(sql: string | null) {
  const escaped = (sql && Buffer.byteLength(sql, "utf8") <= 4096 ? sql : "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
  return `<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main><h1>Console is unavailable.</h1><p>Copy this query to try later, or contact the data team.</p><textarea aria-label="Query for later" readonly>${escaped}</textarea><p>Select and copy, or <a href="/sources">read source details</a>.</p></main></body></html>`;
}
