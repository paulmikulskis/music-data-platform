import { describe, it, expect, vi } from "vitest";
import { uiScript } from "../src/ui.js";

// Execute the shipped enhancement script against a deferred network and a small DOM boundary.
function harness() {
  const current = {
    dataset: { paused: "false", liveUrl: "/functions/fixture_accounts/part/live" },
    hasAttribute: () => true,
    querySelectorAll: () => [
      { dataset: { runId: "newer", status: "succeeded" } },
      { dataset: { runId: "older", status: "running" } },
    ],
    replaceWith: vi.fn(),
    querySelector: () => null,
  };
  const pills: Array<{
    className: string;
    textContent: string;
    href: string;
    dataset: Record<string, string>;
  }> = [];
  const next = {
    classList: { add: vi.fn() },
    querySelector: () => ({
      append: (pill: (typeof pills)[number]) => pills.push(pill),
    }),
  };
  const row = {
    dataset: { runId: "older", status: "partial", landed: "0", rejected: "18" },
  };
  const parsed = {
    getElementById: (id: string) => (id === "recent-runs" ? next : null),
    querySelectorAll: () => [row],
  };
  let resolveFetch!: (value: unknown) => void;
  const fetch = vi.fn(
    () =>
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
  );
  const document = {
    getElementById: (id: string) => (id === "recent-runs" ? current : null),
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener: vi.fn(),
    createElement: () => ({
      className: "",
      textContent: "",
      href: "",
      dataset: {},
      setAttribute: vi.fn(),
      remove: vi.fn(),
    }),
  };
  const client = new Function(
    "document",
    "window",
    "fetch",
    "DOMParser",
    "setTimeout",
    "location",
    uiScript + ";return { refreshRuns, loadPart };",
  )(
    document,
    { addEventListener: vi.fn() },
    fetch,
    class {
      parseFromString() {
        return parsed;
      }
    },
    vi.fn(),
    { href: "http://fixture/functions/fixture_accounts" },
  );
  return {
    current,
    pills,
    refresh: client.refreshRuns,
    loadPart: client.loadPart,
    listeners: document.addEventListener,
    fetch,
    respond: (ok = true) => resolveFetch({ ok, text: async () => "<fixture>" }),
  };
}
describe("run polling regression boundaries", () => {
  it("preserves a pause made while the response is pending", async () => {
    const h = harness();
    const pending = h.refresh();
    h.current.dataset.paused = "true";
    h.respond();
    await pending;
    expect(h.current.replaceWith).not.toHaveBeenCalled();
    expect(h.pills).toHaveLength(0);
  });
  it("announces the older transitioning run with its real status and receipt link", async () => {
    const h = harness();
    const pending = h.refresh();
    h.respond();
    await pending;
    expect(h.current.replaceWith).toHaveBeenCalledOnce();
    expect(h.fetch).toHaveBeenCalledWith("/functions/fixture_accounts/part/live", {
      credentials: "same-origin",
    });
    expect(h.pills).toMatchObject([
      {
        className: "badge partial",
        textContent: "partial · 18 rejected",
        href: "/runs/older",
      },
    ]);
  });
});

it("keeps a recovered disclosure loaded when it opens again", async () => {
  const h = harness();
  const disclosure = {
    dataset: {
      loaded: "",
      lazy: "/functions/fixture/part/receipts",
      part: "receipts",
    },
    open: true,
    matches: (selector: string) => selector === "[data-lazy]",
    querySelector: () => host,
  };
  const host = {
    dataset: {},
    closest: () => disclosure,
    querySelectorAll: () => [],
    replaceChildren: vi.fn(),
    append: vi.fn(),
  };
  const failed = h.loadPart(host, disclosure.dataset.lazy, "receipts");
  h.respond(false);
  await failed;
  expect(disclosure.dataset.loaded).toBe("");
  const retry = h.loadPart(host, disclosure.dataset.lazy, "receipts");
  h.respond();
  await retry;
  for (const [event, listener] of h.listeners.mock.calls)
    if (event === "toggle") await listener({ target: disclosure });
  expect(h.fetch).toHaveBeenCalledTimes(2);
});
it("lets staff row tabs follow their no-JavaScript link", async () => {
  const h = harness();
  const preventDefault = vi.fn();
  const target = {
    closest: (selector: string) => (selector === "[data-rows-tab]" ? {} : null),
  };
  for (const [event, listener] of h.listeners.mock.calls)
    if (event === "click") await listener({ target, preventDefault });
  expect(preventDefault).not.toHaveBeenCalled();
  expect(h.fetch).not.toHaveBeenCalled();
});
