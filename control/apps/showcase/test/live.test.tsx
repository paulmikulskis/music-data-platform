import type { ReactNode } from "react";
import type { ContractRouterClient } from "@orpc/contract";
import type { contract } from "@mdp/contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
import { localTime } from "../components/local-time";

type Client = ContractRouterClient<typeof contract>;
type Alert = Awaited<ReturnType<Client["alerts"]["list"]>>[number];
const mocks = vi.hoisted(() => ({
  list: vi.fn<Client["alerts"]["list"]>(),
  events: vi.fn<Client["platform"]["events"]>(),
}));
vi.mock("server-only", () => ({}));
vi.mock("../server/room", () => ({
  room: async () => ({ person: {}, csrf_token: "fixture" }),
  unavailable: () => null,
}));
vi.mock("../server/clients", () => ({
  controlClient: () => ({ alerts: { list: mocks.list } }),
}));
vi.mock("../server/platform", () => ({
  events: mocks.events,
  sources: async () => null,
}));
vi.mock("../components/shell", () => ({
  Shell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
import Page from "../app/live/page";

const failedAt = "2026-09-27T03:20:00.000Z";
const nextAt = "2026-09-27T04:54:00.000Z";
const dailyFailure: Alert = {
  id: "00000000-0000-4000-8000-000000000001",
  class: "cadence_failed",
  severity: "critical",
  subject_type: "cycle",
  subject_id: "00000000-0000-4000-8000-000000000002",
  run_id: null,
  opened_at: failedAt,
  acknowledged_by: null,
  resolved_at: null,
  resolved_by: null,
  resolution_reason: null,
  runbook_slug: "cadence-failed",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.list.mockResolvedValue([]);
  mocks.events.mockResolvedValue({
    events: [],
    next_cursor: "fixture",
    overlap_start: null,
    has_more: false,
    next_step: "Open /ops.",
    runner: {
      state: "idle",
      busy: false,
      sessions: [],
      next_scheduled_at: nextAt,
      next_step: "Open /ops.",
    },
  });
});

it.each([false, true])(
  "keeps a failed daily visible with an idle runner (acknowledged: %s)",
  async (acknowledged) => {
    mocks.list.mockImplementation(async (input) =>
      input.acknowledged === acknowledged
        ? [
            {
              ...dailyFailure,
              acknowledged_by: acknowledged ? "fixture" : null,
            },
          ]
        : [],
    );
    const html = renderToStaticMarkup(await Page());
    expect(html).toContain("Collection needs attention.");
    expect(html).toContain(`dateTime="${failedAt}"`);
    expect(html).toContain("Next collection from");
    expect(html).toContain(`dateTime="${nextAt}"`);
    expect(html).toContain('href="/sources?view=sources"');
    expect(html).not.toMatch(/Waiting for the next read|Traceback|Retry|<form/);
    expect(mocks.list).toHaveBeenCalledWith({
      class: "cadence_failed",
      resolved: false,
      acknowledged,
    });
  },
);

it("clears attention when the failure resolves", async () => {
  mocks.list.mockResolvedValue([{ ...dailyFailure, resolved_at: nextAt }]);
  const html = renderToStaticMarkup(await Page());
  expect(html).not.toContain("needs attention");
  expect(html).toContain("Waiting for the next read.");
});

it("keeps the newest failure time across acknowledgment states", async () => {
  mocks.list.mockImplementation(async (input) => [
    {
      ...dailyFailure,
      opened_at: input.acknowledged ? failedAt : "2026-09-26T03:20:00.000Z",
    },
  ]);
  const html = renderToStaticMarkup(await Page());
  expect(html).toContain(`dateTime="${failedAt}"`);
  expect(html).not.toContain('dateTime="2026-09-26T03:20:00.000Z"');
});

it("does not imply a healthy idle state when the alert read fails", async () => {
  mocks.list.mockRejectedValue(new Error("Traceback: upstream unavailable"));
  const html = renderToStaticMarkup(await Page());
  expect(html).toContain(
    "Collection status is unavailable. Refresh to check again.",
  );
  expect(html).not.toMatch(/Waiting for the next read|Traceback/);
});

it("keeps attention when the next collection time is unavailable", async () => {
  mocks.list.mockResolvedValue([dailyFailure]);
  mocks.events.mockRejectedValue(new Error("offline"));
  const html = renderToStaticMarkup(await Page());
  expect(html).toContain("Collection needs attention.");
  expect(html).toContain(
    "Next collection time is unavailable. Refresh to check again.",
  );
});

it("uses local calendar days for nearby times and dates for older failures", () => {
  const now = new Date(2026, 8, 27, 12);
  expect(localTime(new Date(2026, 8, 26, 23).toISOString(), true, now)).toMatch(
    /^Yesterday,/,
  );
  expect(localTime(new Date(2026, 8, 27, 3).toISOString(), true, now)).toMatch(
    /^Today,/,
  );
  expect(localTime(new Date(2026, 8, 28, 2).toISOString(), true, now)).toMatch(
    /^Tomorrow,/,
  );
  expect(
    localTime(new Date(2025, 8, 20, 3).toISOString(), true, now),
  ).toContain("2025");
});
