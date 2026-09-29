import { it, expect } from "vitest";
import { upstreamHeaders, injectBackBar } from "../server/console-policy";

it("keeps admin forms and alerts through a showcase hand-off", () => {
  const html =
    '<html><body><aside>Collection failed. Retry below.</aside><form method="post" action="/actions/retry"><button>Retry</button></form></body></html>';
  const page = injectBackBar(html, "/today");
  expect(page).toContain('<form method="post"');
  expect(page).toContain("Collection failed");
  expect(page).toContain('shadowrootmode="open"');
  const headers = upstreamHeaders(
    new Request("https://showcase.invalid/ops"),
    "personal-key",
  );
  expect(headers.get("x-api-key")).toBe("personal-key");
  expect(headers.has("x-mdp-showcase-view")).toBe(false);
});
