import { expect, it } from "vitest";
import { draftWindowPassed, nextDraftWaiting } from "../lib/draft";

it.each([
  ["2026-03-06", "2026-03-07T22:59:59Z", "2026-03-07T23:00:00Z"],
  ["2026-03-13", "2026-03-14T21:59:59Z", "2026-03-14T22:00:00Z"],
  ["2026-10-30", "2026-10-31T21:59:59Z", "2026-10-31T22:00:00Z"],
  ["2026-11-06", "2026-11-07T22:59:59Z", "2026-11-07T23:00:00Z"],
])(
  "closes the %s window at Saturday 18:00 in New York",
  (week, before, close) => {
    expect(draftWindowPassed(week, new Date(before))).toBe(false);
    expect(draftWindowPassed(week, new Date(close))).toBe(true);
  },
);

it("keeps the next opening in the next year when December ends", () => {
  expect(nextDraftWaiting("2026-12-25")).toBe(
    "Weekly picks open after the scheduled read on Jan 2.",
  );
});
