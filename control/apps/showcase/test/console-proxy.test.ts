import { createServer, type ServerResponse } from "node:http";
import { ReadBudget } from "../server/read-budget";
import { ConsoleOperations } from "../server/console-operations";
import { ConsoleCookies } from "../server/console-cookies";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { createORPCClient } from "@orpc/client";
import { RPCLink } from "@orpc/client/fetch";
import type { ContractRouterClient } from "@orpc/contract";
import { contract, errorHint } from "@mdp/contracts";
import { readTimeoutError } from "../server/read-timeout";
import {
  consoleWeight,
  consoleMutationAllowed,
  consolePath,
  injectBackBar,
  locationFor,
  upstreamHeaders,
} from "../server/console-policy";
vi.mock("server-only", () => ({}));
import { signProof } from "../server/proof-token";
import { proxyConsole, createConsoleProxy } from "../server/console-proxy";
const origin = "https://showcase.example.invalid";
const current = {
  id_hash: "session",
  handle: "quartz",
  csrf_token: "csrf",
  person: {
    handle: "quartz",
    display_name: "Quartz",
    email: "quartz@example.invalid",
    admin_key: "server-key",
    api_key_id: "00000000-0000-4000-8000-000000000001",
  },
};
beforeEach(() => {
  vi.stubEnv("MDP_CONTROL_API_URL", "http://control.internal");
  vi.stubEnv("MDP_SHOWCASE_ORIGIN", origin);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("preserves the typed night timeout through the proxy for a client with no saved read", async () => {
  const proxy = createConsoleProxy(new ReadBudget(), async () =>
    Response.json({ json: readTimeoutError().toJSON() }, { status: 503 }),
  );
  const client: ContractRouterClient<typeof contract> = createORPCClient(
    new RPCLink({
      url: origin + "/rpc",
      headers: { origin, "sec-fetch-site": "same-origin" },
      fetch: async (request) => proxy(request, current),
    }),
  );
  await expect(
    client.platform.night({
      since: "2026-09-26T22:00:00Z",
      until: "2026-09-27T13:00:00Z",
    }),
  ).rejects.toMatchObject({
    defined: true,
    code: "SERVICE",
    status: 503,
    data: {
      error_class: "platform_read_timeout",
      next_step: errorHint("platform_read_timeout").next_step,
    },
  });
});

it("retains the last good night read when the upstream read times out", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(new Response("saved night"))
    .mockResolvedValueOnce(
      Response.json({ json: readTimeoutError().toJSON() }, { status: 503 }),
    );
  const proxy = createConsoleProxy(new ReadBudget(), fetcher);
  const request = () =>
    new Request(origin + "/rpc/platform/night", {
      method: "POST",
      headers: { origin, "sec-fetch-site": "same-origin" },
      body: "window",
    });
  expect(await (await proxy(request(), current)).text()).toBe("saved night");
  const clock = vi.spyOn(Date, "now").mockReturnValue(Date.now() + 30001);
  try {
    const response = await proxy(request(), current);
    expect(response.status).toBe(200);
    expect(response.headers.get("x-showcase-read")).toBe("cached");
    expect(await response.text()).toBe("saved night");
    expect(fetcher).toHaveBeenCalledTimes(2);
  } finally {
    clock.mockRestore();
  }
});
it("accepts only enumerated path boundaries and never a forwarded destination", () => {
  for (const path of ["/ops", "/workbench/run", "/api/tenants", "/ui.js"])
    expect(consolePath(path)).toBe(true);
  for (const path of [
    "/ops-evil",
    "//evil",
    "/ops/%2e%2e/sign-in",
    "/today",
    "/workbench\\evil",
    "/ui.js/evil",
  ])
    expect(consolePath(path)).toBe(false);
});
it("requires exact origin AND Fetch Metadata on every non-GET, including HEAD", () => {
  expect(consoleMutationAllowed(new Request(origin + "/ops"), origin)).toBe(
    true,
  );
  const refusedHeaders: HeadersInit[] = [
    {},
    { origin },
    { origin, "sec-fetch-site": "same-site" },
    { origin: "https://evil.invalid", "sec-fetch-site": "same-origin" },
  ];
  for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
    for (const headers of refusedHeaders)
      expect(
        consoleMutationAllowed(
          new Request(origin + "/actions", { method, headers: headers }),
          origin,
        ),
      ).toBe(false);
    expect(
      consoleMutationAllowed(
        new Request(origin + "/actions", {
          method,
          headers: { origin, "sec-fetch-site": "same-origin" },
        }),
        origin,
      ),
    ).toBe(true);
  }
});
it("strips browser credentials, showcase cookies and forwarded headers", () => {
  const headers = upstreamHeaders(
    new Request(origin, {
      headers: {
        authorization: "Bearer attacker",
        "x-api-key": "attacker",
        "x-forwarded-host": "attacker",
        cookie:
          "__Host-mdp_showcase=private; mdp_workbench_session=work; other=no",
      },
    }),
    "person",
  );
  expect([...headers]).toEqual([["x-api-key", "person"]]);
});
it("rewrites only fixed-upstream locations and allowlisted cookies", () => {
  expect(locationFor("/runs/one", "http://control.internal", origin)).toBe(
    origin + "/runs/one",
  );
  expect(
    locationFor(
      "http://control.internal/ops",
      "http://control.internal",
      origin,
    ),
  ).toBe(origin + "/ops");
  expect(
    locationFor("//evil.invalid/ops", "http://control.internal", origin),
  ).toBeNull();
  expect(locationFor("/sign-in", "http://control.internal", origin)).toBeNull();
  const html = injectBackBar(
    '<html><head><base href="https://evil.invalid/"></head><body class="ops">Console</body></html>',
    origin + "/today",
  );
  expect(html).not.toMatch(/<base\b/i);
  expect(html).toContain('<body class="ops"><showcase-navigation');
  expect(html).toContain('shadowrootmode="open"');
  expect(html).toContain(origin + "/today");
});
it("refuses a cross-site POST before contacting upstream", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect(
    (
      await proxyConsole(
        new Request(origin + "/actions", { method: "POST" }),
        current,
      )
    ).status,
  ).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});
it.each([401, 403])(
  "sends an upstream %i key refusal to the configuration recovery page",
  async (status) => {
    const gate = new ReadBudget();
    gate.setRunner("idle");
    const proxy = createConsoleProxy(
      gate,
      async () => new Response("Refused", { status }),
    );
    const response = await proxy(
      new Request(origin + "/runs/fixture"),
      current,
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      origin + "/sign-in?reason=key-refused",
    );
  },
);
it("keeps a delayed key refusal recoverable from its pending request", async () => {
  const gate = new ReadBudget();
  gate.setRunner("idle");
  const operations = new ConsoleOperations();
  let finish = () => {};
  const fetcher: typeof fetch = async () =>
    new Promise<Response>((resolve) => {
      finish = () => resolve(new Response("Refused", { status: 403 }));
    });
  const proxy = createConsoleProxy(gate, fetcher, 1, operations);
  const response = await proxy(new Request(origin + "/runs/fixture"), current);
  expect(response.status).toBe(202);
  const id = response.headers.get("location")?.split("/").at(-1);
  expect(id).toBeDefined();
  finish();
  await expect
    .poll(() => operations.get(id ?? "", current.id_hash)?.result?.status)
    .toBe(303);
  const result = operations.get(id ?? "", current.id_hash)?.result;
  expect(new Headers(result?.headers).get("location")).toBe(
    origin + "/sign-in?reason=key-refused",
  );
});
it("passes multipart bytes intact and uses manual redirects and the actor key", async () => {
  const body = new FormData();
  body.set(
    "file",
    new Blob(["artist,platform\nfixture,spotify"]),
    "subject.csv",
  );
  const request = new Request(origin + "/actions/import", {
    method: "POST",
    headers: { origin, "sec-fetch-site": "same-origin" },
    body,
  });
  const expected = await request.clone().text();
  const fetcher = vi.fn(async (_url: URL | RequestInfo, init?: RequestInit) => {
    expect(await new Response(init?.body).text()).toBe(expected);
    expect(new Headers(init?.headers).get("x-api-key")).toBe("server-key");
    expect(init?.redirect).toBe("manual");
    return new Response(null, {
      status: 303,
      headers: { Location: "/targets" },
    });
  });
  vi.stubGlobal("fetch", fetcher);
  const response = await proxyConsole(request, current);
  expect(response.status).toBe(303);
  expect(response.headers.get("location")).toBe(origin + "/targets");
});
it("keeps downloads as bytes and injects navigation only into HTML", async () => {
  const gate = new ReadBudget();
  gate.setRunner("idle");
  const proxy = createConsoleProxy(gate);
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response("a,b\n1,2", {
          headers: {
            "content-type": "text/csv",
            "content-disposition": "attachment; filename=rows.csv",
          },
        }),
    ),
  );
  const response = await proxy(new Request(origin + "/api/export"), current);
  expect(await response.text()).toBe("a,b\n1,2");
  expect(response.headers.get("content-disposition")).toContain("rows.csv");
});

it("suppresses cold heavy console callers for both busy and unknown runners", async () => {
  for (const state of ["busy", "unknown"] as const) {
    const gate = new ReadBudget();
    gate.setRunner(state);
    const fetcher = vi.fn();
    const proxy = createConsoleProxy(gate, fetcher);
    for (const path of [
      "/explorer",
      "/sandbox",
      "/workbench",
      "/queries",
      "/api/workbench/query",
      "/rpc/workbench/query",
      "/functions/source/preview.csv",
    ]) {
      const response = await proxy(new Request(origin + path), current);
      expect(response.status).toBe(503);
      expect(await response.text()).toContain("Open /status");
    }
    expect(fetcher).not.toHaveBeenCalled();
    expect(consoleWeight("/actions/create-tenant", "POST")).toBe("light");
  }
});
it("caches and single-flights reads per identity and request; writes carry the actor key", async () => {
  const gate = new ReadBudget();
  gate.setRunner("idle");
  const fetcher = vi.fn(async (_url: URL | RequestInfo, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("x-api-key")).toBe("server-key");
    return new Response("page");
  });
  const proxy = createConsoleProxy(gate, fetcher);
  await Promise.all(
    Array.from({ length: 6 }, () =>
      proxy(new Request(origin + "/ops"), current),
    ),
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
  await proxy(new Request(origin + "/ops"), { ...current, id_hash: "second" });
  expect(fetcher).toHaveBeenCalledTimes(2);
  await proxy(new Request(origin + "/ops?view=other"), current);
  expect(fetcher).toHaveBeenCalledTimes(3);
  await proxy(new Request(origin + "/explorer"), current);
  gate.setRunner("busy");
  const saved = await proxy(new Request(origin + "/explorer"), current);
  expect(saved.headers.get("x-showcase-read")).toBe("busy");
  expect(fetcher).toHaveBeenCalledTimes(4);
  await proxy(
    new Request(origin + "/actions/create-tenant", {
      method: "POST",
      headers: { origin, "sec-fetch-site": "same-origin" },
      body: "fixture",
    }),
    current,
  );
  expect(fetcher).toHaveBeenCalledTimes(5);
});
it("classifies POST RPC reads by operation and caches their identity and input", async () => {
  const gate = new ReadBudget();
  gate.setRunner("idle");
  const fetcher = vi.fn(async () => new Response("read"));
  const proxy = createConsoleProxy(gate, fetcher);
  const request = (path: string, input = "one") =>
    new Request(origin + path, {
      method: "POST",
      headers: {
        origin,
        "sec-fetch-site": "same-origin",
        "content-type": "application/json",
      },
      body: JSON.stringify({ json: { source_key: input } }),
    });
  await Promise.all(
    Array.from({ length: 6 }, () =>
      proxy(request("/rpc/functions/page"), current),
    ),
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
  await proxy(request("/rpc/functions/page", "two"), current);
  await proxy(request("/rpc/functions/page"), {
    ...current,
    id_hash: "second",
  });
  expect(fetcher).toHaveBeenCalledTimes(3);
  for (const state of ["busy", "unknown"] as const) {
    gate.setRunner(state);
    const saved = await proxy(request("/rpc/functions/page"), current);
    expect(saved.headers.get("x-showcase-read")).toBe("busy");
    for (const path of [
      "/rpc/functions/page",
      "/rpc/workbench/query",
      "/rpc/unknown/read",
    ]) {
      const response = await proxy(request(path, state), current);
      expect(response.status).toBe(503);
      expect(await response.text()).toContain("Open /status");
    }
    expect(fetcher).toHaveBeenCalledTimes(3);
    const light = createConsoleProxy(
      gate,
      vi.fn(async () => new Response("metadata")),
    );
    expect(
      (await light(request("/rpc/streamlines/list"), current)).status,
    ).toBe(200);
  }
});
it("keeps admission after a client timeout and disconnect until upstream finishes", async () => {
  const gate = new ReadBudget();
  const operations = new ConsoleOperations();
  const finish: (() => void)[] = [];
  const fetcher = vi.fn(
    async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            finish.push(() => {
              controller.enqueue(new TextEncoder().encode("done"));
              controller.close();
            });
          },
        }),
      ),
  );
  const proxy = createConsoleProxy(gate, fetcher, 15, operations);
  const controller = new AbortController();
  const request = () =>
    new Request(origin + "/actions/canaries", {
      method: "POST",
      headers: { origin, "sec-fetch-site": "same-origin" },
      body: "fixture",
      signal: controller.signal,
    });
  const [one, two] = await Promise.all([
    proxy(request(), current),
    proxy(request(), current),
  ]);
  controller.abort();
  expect(one.status).toBe(202);
  expect(two.status).toBe(202);
  expect(await one.text()).not.toMatch(/retry/i);
  const waiting = proxy(request(), current);
  await new Promise((r) => setTimeout(r, 5));
  expect(fetcher).toHaveBeenCalledTimes(2);
  const id = one.headers.get("location")!.split("/").at(-1)!;
  expect(operations.get(id, "other")).toBeUndefined();
  expect(operations.get(id, current.id_hash)?.result).toBeUndefined();
  finish[0]!();
  await new Promise((r) => setTimeout(r, 1));
  expect(fetcher).toHaveBeenCalledTimes(3);
  finish[1]!();
  finish[2]!();
  expect([200, 202]).toContain((await waiting).status);
  await expect
    .poll(() => operations.get(id, current.id_hash)?.result?.status)
    .toBe(200);
});
it("stores workbench cookies by showcase session and strips every upstream Set-Cookie", async () => {
  const gate = new ReadBudget();
  gate.setRunner("idle");
  const seen: (string | null)[] = [];
  const proxy = createConsoleProxy(gate, async (_url, init) => {
    seen.push(new Headers(init?.headers).get("cookie"));
    return new Response("workbench", {
      headers: {
        "set-cookie":
          "mdp_workbench_session=internal; Path=/; Domain=other.invalid",
      },
    });
  });
  const request = () =>
    new Request(origin + "/workbench", {
      method: "POST",
      headers: {
        origin,
        "sec-fetch-site": "same-origin",
        cookie: "mdp_workbench_session=attacker",
      },
    });
  const first = await proxy(request(), current);
  expect(first.headers.get("set-cookie")).toBeNull();
  await proxy(request(), current);
  await proxy(request(), { ...current, id_hash: "second" });
  expect(seen).toEqual([null, "mdp_workbench_session=internal", null]);
  const jar = new ConsoleCookies();
  jar.save("one", ["mdp_workbench_session=value; Max-Age=60"]);
  jar.save("one", ["mdp_workbench_session=; Max-Age=0"]);
  expect(jar.get("one")).toBeUndefined();
});

it("keeps the actual upstream socket open after the gateway response deadline", async () => {
  const responses: ServerResponse[] = [];
  let closed = 0;
  const server = createServer((_request, response) => {
    responses.push(response);
    response.once("close", () => closed++);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("The test server needs a TCP port. Rerun the test.");
  vi.stubEnv("MDP_CONTROL_API_URL", `http://127.0.0.1:${address.port}`);
  const operations = new ConsoleOperations();
  const proxy = createConsoleProxy(new ReadBudget(), fetch, 30, operations);
  const controller = new AbortController();
  try {
    const result = await proxy(
      new Request(origin + "/actions/canaries", {
        method: "POST",
        headers: { origin, "sec-fetch-site": "same-origin" },
        body: "fixture",
        signal: controller.signal,
      }),
      current,
    );
    expect(result.status).toBe(202);
    controller.abort();
    await expect.poll(() => responses.length).toBe(1);
    expect(closed).toBe(0);
    const id = result.headers.get("location")!.split("/").at(-1)!;
    expect(operations.get(id, current.id_hash)?.result).toBeUndefined();
    responses[0]!.end("finished");
    await expect
      .poll(() => operations.get(id, current.id_hash)?.result?.status)
      .toBe(200);
  } finally {
    responses.forEach((response) => response.end());
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

it("keeps operator link minting outside the public console proxy", () => {
  expect(consolePath("/rpc/showcase/link")).toBe(false);
  expect(consolePath("/api/showcase/link")).toBe(false);
});

it("keeps signed reading context through links, filters and redirects without sharing operator pages", async () => {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  const token = signProof({
    handle: current.handle,
    level: "cycle",
    song: "",
    back: "/songs?view=places",
  });
  const calls: string[] = [];
  const proxy = createConsoleProxy(new ReadBudget(), async (_input, init) => {
    calls.push(
      new Headers(init?.headers).get("x-mdp-showcase-view") ?? "operator",
    );
    return new Response(
      '<html><head></head><body><a href="/runs/one">Read</a><form method="get"><button>Filter</button></form></body></html>',
      { headers: { "content-type": "text/html", location: "/runs/one" } },
    );
  });
  const proof = await proxy(
    new Request(origin + "/functions?showcase_proof=" + token),
    current,
  );
  const html = await proof.text();
  expect(html).toContain('name="showcase_proof"');
  expect(html).toContain("/runs/one?showcase_proof=");
  expect(proof.headers.get("location")).toContain("showcase_proof=");
  expect(html).toContain(origin + "/songs?view=places");
  expect(html).toContain("You&#39;re in the operator console");
  expect(html).not.toContain("read-only");
  await proxy(
    new Request(origin + "/functions", {
      headers: { "x-mdp-showcase-view": "proof" },
    }),
    current,
  );
  expect(calls).toEqual(["operator", "operator"]);
  const write = await proxy(
    new Request(origin + "/actions/retry?showcase_proof=" + token, {
      method: "POST",
      headers: { origin, "sec-fetch-site": "same-origin" },
    }),
    current,
  );
  expect(write.status).toBe(200);
  expect(calls).toEqual(["operator", "operator", "operator"]);
  const expired = await proxy(
    new Request(origin + "/functions?showcase_proof=expired"),
    current,
  );
  expect(expired.headers.get("location")).toBe(origin + "/live");
});

it("keeps the return link and safe status when a proof handoff cannot reach the console", async () => {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  const token = signProof({
    handle: current.handle,
    level: "source",
    song: "",
    back: "/songs?view=places",
  });
  const proxy = createConsoleProxy(new ReadBudget(), async () => {
    throw new Error("offline");
  });
  const response = await proxy(
    new Request(origin + "/functions?showcase_proof=" + token),
    current,
  );
  expect(response.status).toBe(503);
  const html = await response.text();
  expect(html).toContain(origin + "/songs?view=places");
  expect(html).toContain('href="/live"');
  expect(html).not.toContain('href="/status"');
});

it("keeps an escaped query and an independent next step during a Workbench outage", async () => {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  const proxy = createConsoleProxy(new ReadBudget(), async () => {
    throw new Error("Offline");
  });
  const token = signProof({
    level: "source",
    song: "",
    handle: current.handle,
    back: "/?trace=home.playlist_adds",
  });
  const sql = "SELECT '</textarea><script>window.bad=1</script>'";
  const result = await proxy(
    new Request(
      origin +
        "/workbench?" +
        new URLSearchParams({ intent: "query", sql, showcase_proof: token }),
    ),
    current,
  );
  expect(result.status).toBe(503);
  const html = await result.text();
  expect(html).toContain("Console is unavailable.");
  expect(html).toContain("&lt;/textarea&gt;&lt;script&gt;");
  expect(html).not.toContain("<script>window.bad");
  expect(html).toContain('href="/sources"');
});
