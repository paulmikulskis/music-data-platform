import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { z } from "zod";
import type { DB } from "../src/db.js";
import { app } from "../src/app.js";
import { authenticate, type Identity } from "../src/auth.js";

vi.mock("../src/auth.js", async (original) => ({
  ...(await original<typeof import("../src/auth.js")>()),
  authenticate: vi.fn(),
}));
vi.mock("../src/db.js", async (original) => ({
  ...(await original<typeof import("../src/db.js")>()),
  database: () => ({
    unsafe: vi.fn().mockResolvedValue([]),
    begin: async (body: (db: DB) => Promise<unknown>) =>
      body({ unsafe: vi.fn().mockResolvedValue([]) }),
  }),
}));

const analyst: Identity = {
  actor: "viewer",
  admin: false,
  staff: true,
  tenant_id: null,
  tenant_slug: null,
};
const auditRows = [
  {
    actor: "staff:viewer",
    occurred_at: "2026-01-01T00:00:00Z",
    cross_tenant: false,
    unresolved: false,
    tenants: [],
    labels: {},
    query_hash: "own-hash",
  },
  {
    actor: "staff:other",
    occurred_at: "2026-01-02T00:00:00Z",
    cross_tenant: true,
    unresolved: true,
    tenants: ["one", "two"],
    labels: {},
    query_hash: "other-hash",
  },
];
const forwardedIdentity = z.object({
  userId: z.string(),
  staff: z.boolean(),
  warehouseRole: z.null(),
});

beforeEach(() => {
  vi.stubEnv("MDP_WORKBENCH_URL", "http://workbench.invalid");
  vi.stubEnv("MDP_WORKBENCH_SERVICE_TOKEN", "fixture");
  vi.mocked(authenticate).mockResolvedValue(analyst);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

function service(expected: z.infer<typeof forwardedIdentity>) {
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    expect(String(url)).toBe("http://workbench.invalid/v1/workbench/queries");
    expect(new Headers(init?.headers).get("authorization")).toBe(
      "Bearer fixture",
    );
    const body = forwardedIdentity.parse(JSON.parse(String(init?.body)));
    expect(body).toEqual(expected);
    return Response.json({
      queries: body.staff
        ? auditRows.filter((row) => row.actor === body.userId)
        : auditRows,
    });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

it("renders every actor for an admin reviewer", async () => {
  vi.mocked(authenticate).mockResolvedValue({
    ...analyst,
    admin: true,
    staff: false,
  });
  service({ userId: "viewer", staff: false, warehouseRole: null });
  const response = await app.request("/queries");
  expect(response.status).toBe(200);
  const html = await response.text();
  expect(html).toContain("own-hash");
  expect(html).toContain("other-hash");
  expect(html).toContain("Mixes tenant data");
});

it("renders only the analyst's rows and ignores identity query parameters", async () => {
  service({ userId: "staff:viewer", staff: true, warehouseRole: null });
  const response = await app.request("/queries?userId=other&staff=false");
  expect(response.status).toBe(200);
  const html = await response.text();
  expect(html).toContain("own-hash");
  expect(html).not.toContain("other-hash");
  expect(html).not.toContain("staff:other");
  expect(html).not.toContain("Mixes tenant data");
});

it("ignores forged identity fields on the queries API", async () => {
  const fetcher = service({
    userId: "staff:viewer",
    staff: true,
    warehouseRole: null,
  });
  const response = await app.request("/api/workbench/queries", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      userId: "other",
      staff: false,
      warehouseRole: "analyst_other",
    }),
  });
  expect(response.status).toBe(200);
  expect(fetcher).toHaveBeenCalledOnce();
  const body = await response.text();
  expect(body).toContain("own-hash");
  expect(body).not.toContain("other-hash");
});

it.each(["service", "network"])(
  "offers Workbench when the %s fails",
  async (failure) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => {
        if (failure === "network") throw new Error("private diagnostic");
        return Response.json(
          { error_class: "workbench_failed", message: "private diagnostic" },
          { status: 500 },
        );
      }),
    );
    const response = await app.request("/queries");
    // The page remains usable through the Console proxy, which replaces 5xx bodies.
    expect(response.status).toBe(200);
    const html = await response.text();
    expect(html).toContain("Query history is unavailable right now.");
    expect(html).toContain(
      '<a href="/workbench">Open Workbench</a> to run a query.',
    );
    expect(html).toContain('data-error-class="query_history_unavailable"');
    expect(html).not.toContain("private diagnostic");
  },
);

it("refuses viewers without staff or admin access before reading history", async () => {
  vi.mocked(authenticate).mockResolvedValue({ ...analyst, staff: false });
  const fetcher = service({
    userId: "staff:viewer",
    staff: true,
    warehouseRole: null,
  });
  expect((await app.request("/queries")).status).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});
