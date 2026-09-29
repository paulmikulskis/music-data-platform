import { afterEach, expect, it, vi } from "vitest";
import { useEffect } from "react";
vi.mock("react", () => ({ useEffect: vi.fn() }));
import { ClearLink } from "../components/clear-link";

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

it.each(["timeout", "expired", "key-refused", "ended", null] as const)(
  "clears the token and keeps only %s recovery in browser history",
  (reason) => {
    const replaceState = vi.fn();
    vi.stubGlobal("window", { history: { replaceState } });
    ClearLink({ reason });
    const [effect] = vi.mocked(useEffect).mock.calls[0]!;
    effect();
    expect(replaceState).toHaveBeenCalledExactlyOnceWith(
      null,
      "",
      reason ? `/sign-in?reason=${reason}` : "/sign-in",
    );
  },
);
