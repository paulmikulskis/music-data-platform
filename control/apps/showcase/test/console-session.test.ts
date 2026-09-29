import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { randomUUID } from "node:crypto";
import postgres from "postgres";
import { createORPCClient } from "@orpc/client";
import { RPCLink } from "@orpc/client/fetch";
import type { ContractRouterClient } from "@orpc/contract";
import { contract, errorHint } from "@mdp/contracts";
import { hash, validateSession } from "@mdp/showcase-auth";
vi.mock("server-only", () => ({}));
vi.mock("../server/session", () => ({ session: vi.fn() }));
vi.mock("../server/heartbeat", () => ({ heartbeat: { refresh: vi.fn() } }));
vi.mock("../server/console-proxy", () => ({ proxyConsole: vi.fn() }));
import { GET, POST } from "../app/[...console]/route";
import { session } from "../server/session";
import { heartbeat } from "../server/heartbeat";
import { proxyConsole } from "../server/console-proxy";
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(session).mockResolvedValue(null);
});
afterEach(() => vi.unstubAllEnvs());
it("keeps an ended console session on the session-ended page", async () => {
  const response = await GET(
    new Request("https://showcase.invalid/runs/fixture"),
  );
  expect(response.status).toBe(303);
  expect(response.headers.get("location")).toBe("/sign-in");
  expect(proxyConsole).not.toHaveBeenCalled();
});

const url = process.env.MDP_SHOWCASE_TEST_URL;
it.skipIf(!url)(
  "returns a typed retry response while session reads are blocked, then validates again",
  async () => {
    const local = new URL(url!);
    expect(["127.0.0.1", "localhost"]).toContain(local.hostname);
    const db = postgres(local.toString(), {
      max: 4,
      connection: { lock_timeout: 100, statement_timeout: 2000 },
    });
    local.username = "postgres";
    local.password = "postgres";
    const owner = postgres(local.toString(), { onnotice: () => {} });
    const person = {
      handle: "timeout",
      display_name: "Timeout fixture",
      email: "timeout@example.invalid",
      admin_key: "test-key",
      api_key_id: randomUUID(),
    };
    const token = "t".repeat(43);
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([person]));
    vi.mocked(session).mockImplementation(() => validateSession(db, token));
    const lock = await owner.reserve();
    try {
      await owner`INSERT INTO control.showcase_session(id_hash,handle,csrf_token,expires_at)
      VALUES (${hash(token)},${person.handle},'test-csrf',now()+interval '1 day')`;
      await lock`BEGIN`;
      await lock`LOCK TABLE control.showcase_session IN ACCESS EXCLUSIVE MODE`;
      const client: ContractRouterClient<typeof contract> = createORPCClient(
        new RPCLink({
          url: "https://showcase.invalid/rpc",
          fetch: async (request) => {
            const response = await POST(request);
            expect(response.status).toBe(503);
            expect(response.headers.get("cache-control")).toBe("no-store");
            expect(response.headers.get("retry-after")).toBe("1");
            return response;
          },
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
      expect(proxyConsole).not.toHaveBeenCalled();
      expect(heartbeat.refresh).not.toHaveBeenCalled();
      await lock`ROLLBACK`;
      vi.mocked(proxyConsole).mockResolvedValue(new Response("checked"));
      expect(
        (
          await POST(
            new Request("https://showcase.invalid/rpc/platform/night", {
              method: "POST",
            }),
          )
        ).status,
      ).toBe(200);
      expect(proxyConsole).toHaveBeenCalledOnce();
      expect(heartbeat.refresh).toHaveBeenCalledWith(person);
      await owner`UPDATE control.showcase_session SET revoked_at=now() WHERE id_hash=${hash(token)}`;
      expect(
        (await GET(new Request("https://showcase.invalid/runs"))).status,
      ).toBe(303);
      expect(proxyConsole).toHaveBeenCalledOnce();
    } finally {
      await lock`ROLLBACK`;
      lock.release();
      await owner`DELETE FROM control.showcase_session WHERE id_hash=${hash(token)}`;
      await owner`DELETE FROM control.showcase_actor WHERE api_key_id=${person.api_key_id}`;
      await Promise.all([db.end(), owner.end()]);
    }
  },
);

it("does not disguise an unrelated session failure as a retryable timeout", async () => {
  vi.mocked(session).mockRejectedValue(new Error("fixture failure"));
  await expect(
    GET(new Request("https://showcase.invalid/runs")),
  ).rejects.toThrow("fixture failure");
  expect(proxyConsole).not.toHaveBeenCalled();
});
