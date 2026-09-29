import { it, expect, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../server/session", () => ({
  session: async () => ({ handle: "fixture" }),
}));
import { GET } from "../app/s/apps/route";
// Hono uses its own JSX factory. Load it at runtime outside Next's React type graph.
const controlModule = "../../control-api/src/app";
const { app } = await import(controlModule);
it("marks the operator console live using the real control app health route", async () => {
  vi.stubEnv("MDP_CONTROL_API_URL", "http://control.invalid");
  const paths: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const parsed = new URL(url);
      if (parsed.hostname !== "control.invalid")
        throw new Error("External probes are outside this local test.");
      paths.push(parsed.pathname);
      return app.fetch(new Request(url));
    }),
  );
  try {
    expect((await (await GET()).json()).apps[0]).toMatchObject({
      name: "Console",
      state: "live",
    });
    expect(paths).toEqual(["/health"]);
  } finally {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  }
});
