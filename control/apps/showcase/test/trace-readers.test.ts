import { beforeEach, describe, expect, it, vi } from "vitest";
import { syntheticSources } from "./synthetic-fixture";
import { traceView } from "../lib/trace";
import {
  chains,
  stageOf,
  readerForNode,
  nearestCountedStep,
} from "../components/lineage/layout";

vi.mock("server-only", () => ({}));
vi.mock("../server/clients", () => ({ controlStore: () => async () => [] }));
vi.mock("../server/platform", () => ({ sources: vi.fn() }));
vi.mock("../server/reads", () => ({ movers: vi.fn() }));
vi.mock("../server/lineage-counts", () => ({ lineageCounts: async () => [] }));
vi.mock("../server/trace-evidence", () => ({ traceReceipts: async () => [] }));
vi.mock("../server/artifacts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../server/artifacts")>()),
  readArtifacts: () => {
    throw new Error("No artifact");
  },
}));
import { sources } from "../server/platform";
import { traceData } from "../server/trace";
const person = {
  handle: "fixture",
  display_name: "Fixture",
  admin_key: "fixture",
  api_key_id: "00000000-0000-4000-8000-000000000001",
};
beforeEach(() => {
  vi.mocked(sources).mockReset();
  vi.mocked(sources).mockResolvedValue({
    state: "live",
    savedAt: syntheticSources.queried_at,
    value: syntheticSources,
  });
});

describe("reader dates from production evidence", () => {
  it.each(["bc_discover", "bc_daily_list", "bc_radio"])(
    "keeps %s switched off despite its historical read",
    async (source) => {
      const view = traceView.parse(
        await traceData(person, { entry: `source.${source}` }),
      );
      const node = view.nodes.find((row) => row.id === `fn:${source}`);
      expect(node).toMatchObject({ enabled: false, paused: false });
      expect(node?.last_read).toBeTruthy();
    },
  );
  it("carries a paused incident into the lineage response", async () => {
    const source = syntheticSources.sources.find(
      (row) => row.source_key === "bc_discover",
    );
    if (!source?.evidence)
      throw new Error("Open the production source fixture.");
    vi.mocked(sources).mockResolvedValue({
      state: "live",
      savedAt: syntheticSources.queried_at,
      value: {
        ...syntheticSources,
        sources: [
          {
            ...source,
            evidence: {
              ...source.evidence,
              incident: {
                alert_id: "00000000-0000-4000-8000-000000000001",
                run_id: null,
                attempt_no: null,
                class: "provider_credentials_missing",
                at: source.evidence.checked_at,
                remediation: "Open source details.",
              },
            },
          },
        ],
      },
    });
    const view = traceView.parse(
      await traceData(person, { entry: "source.bc_discover" }),
    );
    expect(view.nodes.find((row) => row.id === "fn:bc_discover")).toMatchObject(
      { enabled: false, paused: true },
    );
  });
  it.each(["am_playlist", "bc_daily_list", "am_playlist_weekly"])(
    "uses the successful run for every reader-written step of %s",
    async (source) => {
      const view = traceView.parse(
        await traceData(person, { entry: `source.${source}` }),
      );
      const path = chains(view)[0];
      expect(path).toBeDefined();
      const nodes = view.nodes.filter((node) => path.includes(node.id));
      const current = syntheticSources.sources.find(
        (item) => item.source_key === source,
      )!;
      for (const stage of ["source", "readers", "collected"]) {
        const node = nodes.find((item) => item.stage === stage)!;
        expect(node).toBeDefined();
        expect(readerForNode(node, nodes, view.edges)).toMatchObject({
          source,
          last_read: current.evidence?.last_success ?? null,
          enabled: current.enabled,
        });
      }
      expect(sources).toHaveBeenCalledTimes(1);
    },
  );
  it("does not mistake a load or an attempt for a successful read", async () => {
    const source = syntheticSources.sources.find(
      (item) => item.source_key === "am_playlist",
    )!;
    expect(source.last_read).not.toBe(source.evidence?.last_success);
    vi.mocked(sources).mockResolvedValue({
      state: "live",
      savedAt: syntheticSources.queried_at,
      value: { ...syntheticSources, sources: [{ ...source, evidence: null }] },
    });
    const view = await traceData(person, { entry: "source.am_playlist" });
    expect(
      view.nodes.find((node) => node.id === "fn:am_playlist")?.last_read,
    ).toBeNull();
  });
  it("keeps dates unknown when the shared source read fails", async () => {
    vi.mocked(sources).mockRejectedValue(new Error("Unavailable"));
    const view = await traceData(person, { entry: "source.am_playlist" });
    expect(view.nodes.every((node) => node.last_read === null)).toBe(true);
    expect(view.nodes.every((node) => node.enabled === null)).toBe(true);
  });
  it("uses the selected writer of a shared table and leaves matching steps alone", async () => {
    for (const source of [
      "am_playlist",
      "sp_playlist",
      "bc_daily_list",
      "am_playlist_weekly",
    ]) {
      const view = await traceData(person, { entry: `source.${source}` });
      const path = chains(view).find((path) => path.includes(`fn:${source}`))!;
      expect(path).toBeDefined();
      const nodes = view.nodes.filter((node) => path.includes(node.id));
      const received = nodes.find((node) => node.stage === "collected")!;
      expect(readerForNode(received, nodes, view.edges)?.source).toBe(source);
      const ready = nodes.find((node) => stageOf(node.stage) === "ready")!;
      expect(readerForNode(ready, nodes, view.edges)).toBeNull();
      const counted = {
        ...ready,
        count: {
          value: "12",
          captured_at: syntheticSources.queried_at,
          build_key: "fixture",
          build: {
            relation: ready.relation ?? "",
            cycle_id: "fixture",
            close_no: "1",
            built_at: syntheticSources.queried_at,
            scope: "global" as const,
            stamped: true as const,
            tenant_slug: null,
          },
        },
      };
      expect(
        nearestCountedStep(
          received,
          [...nodes.filter((node) => node.id !== ready.id), counted],
          path,
        )?.id,
      ).toBe(ready.id);
      expect(nearestCountedStep(received, nodes, path)).toBeUndefined();
    }
  });
});
