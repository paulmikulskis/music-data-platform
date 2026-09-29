import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({ events: vi.fn(), session: vi.fn() }));
vi.mock("../server/platform", () => ({ events: mocks.events }));
import { Heartbeat } from "../server/heartbeat";
const person = {
  handle: "fixture",
  display_name: "Test viewer",
  email: "fixture@example.invalid",
  admin_key: "server-only",
  api_key_id: "key",
};
beforeEach(() => {
  vi.useFakeTimers();
  mocks.events.mockReset();
});
afterEach(() => vi.useRealTimers());
it("shares one poll, remembers event ids and replays them to a reconnecting client", async () => {
  const hub = new Heartbeat();
  const one = vi.fn();
  const two = vi.fn();
  mocks.events.mockResolvedValue({
    runner: { state: "idle" },
    events: [
      {
        key: "cycle_closed:fixture",
        occurred_at: new Date().toISOString(),
        kind: "cycle_closed",
        scheduled: true,
      },
    ],
    next_cursor: "opaque-cursor",
    overlap_start: null,
    has_more: false,
  });
  const stop1 = hub.subscribe({ person, send: one }, "0");
  const stop2 = hub.subscribe({ person, send: two }, "0");
  await hub.refresh(person);
  expect(mocks.events).toHaveBeenCalledTimes(1);
  expect(one).toHaveBeenCalledWith(
    expect.objectContaining({ key: "cycle_closed:fixture" }),
    true,
    expect.anything(),
  );
  stop1();
  const three = vi.fn();
  const stop3 = hub.subscribe(
    { person, send: three },
    Buffer.from(JSON.stringify({ key: "earlier-event" })).toString("base64url"),
  );
  expect(three).toHaveBeenCalledWith(
    expect.objectContaining({ key: "cycle_closed:fixture" }),
    true,
    expect.anything(),
  );
  await vi.advanceTimersByTimeAsync(10000);
  expect(mocks.events).toHaveBeenLastCalledWith(person, "opaque-cursor");
  stop2();
  stop3();
  await vi.advanceTimersByTimeAsync(30000);
  expect(mocks.events).toHaveBeenCalledTimes(2);
});
it("reports reconnecting rather than a fabricated pulse on a failed poll", async () => {
  mocks.events.mockRejectedValue(Error("offline"));
  const hub = new Heartbeat();
  const send = vi.fn();
  const stop = hub.subscribe({ person, send }, "0");
  await hub.refresh(person);
  expect(send).toHaveBeenLastCalledWith(null, false, expect.anything());
  stop();
});
it("starts polling when a room refresh finishes before the first stream subscribes", async () => {
  mocks.events.mockResolvedValue({
    runner: { state: "idle" },
    events: [],
    next_cursor: "opaque",
    overlap_start: null,
    has_more: false,
  });
  const hub = new Heartbeat();
  await hub.refresh(person);
  const stop = hub.subscribe({ person, send: vi.fn() }, "");
  await vi.advanceTimersByTimeAsync(10000);
  expect(mocks.events).toHaveBeenCalledTimes(2);
  stop();
});
it("resumes a fresh poller from the API cursor carried by the stream id", async () => {
  mocks.events.mockResolvedValue({
    runner: { state: "idle" },
    events: [],
    next_cursor: "next",
    overlap_start: null,
    has_more: false,
  });
  const hub = new Heartbeat();
  const id = Buffer.from(
    JSON.stringify({ cursor: "last-page", key: "run_settled:prior" }),
  ).toString("base64url");
  const stop = hub.subscribe({ person, send: vi.fn() }, id);
  await hub.refresh(person);
  expect(mocks.events).toHaveBeenCalledWith(person, "last-page");
  stop();
});

it("does not replay a saved backlog to a new tab without a resume cursor", async () => {
  const hub = new Heartbeat();
  mocks.events.mockResolvedValue({
    runner: { state: "idle" },
    events: Array.from({ length: 500 }, (_, i) => ({
      key: "run_settled:" + i,
      occurred_at: new Date().toISOString(),
      kind: "run_settled",
      scheduled: true,
    })),
    next_cursor: "opaque",
    overlap_start: null,
    has_more: false,
  });
  await hub.refresh(person);
  const send = vi.fn();
  const stop = hub.subscribe({ person, send }, "");
  expect(send).toHaveBeenCalledTimes(1);
  expect(send).toHaveBeenCalledWith(
    null,
    true,
    expect.objectContaining({ state: "idle" }),
  );
  stop();
});

it("defers additional history pages while scheduled work is busy", async () => {
  mocks.events.mockResolvedValue({
    runner: { state: "busy" },
    events: [],
    next_cursor: "next-page",
    overlap_start: null,
    has_more: true,
  });
  const hub = new Heartbeat();
  const stop = hub.subscribe({ person, send: vi.fn() }, "");
  await hub.refresh(person);
  expect(mocks.events).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(10000);
  expect(mocks.events).toHaveBeenCalledTimes(2);
  expect(mocks.events).toHaveBeenLastCalledWith(person, "next-page");
  stop();
});

it.each([{ cursor: 42, key: "prior" }, { cursor: "prior", key: 42 }, null])(
  "ignores malformed resume data",
  async (checkpoint) => {
    mocks.events.mockResolvedValue({
      runner: { state: "idle" },
      events: [],
      next_cursor: "next",
      overlap_start: null,
      has_more: false,
    });
    const hub = new Heartbeat();
    const id = Buffer.from(JSON.stringify(checkpoint)).toString("base64url");
    const stop = hub.subscribe({ person, send: vi.fn() }, id);
    await hub.refresh(person);
    expect(mocks.events).toHaveBeenCalledWith(person, undefined);
    stop();
  },
);
