import { afterEach, expect, it, vi } from "vitest";
import { readFileSync, rmSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
vi.mock("server-only", () => ({}));
import { LinkOut } from "../components/link-out";
import { linkHref } from "../lib/links";
import { readLinks } from "../server/links";
import { linkFixture } from "./link-fixture";
import fixture from "./fixtures/links.json";
import policy from "../lib/card-words.json";

const folders: string[] = [];
function reader(value?: unknown) {
  const directory = linkFixture(value);
  folders.push(directory);
  return readLinks(directory);
}
afterEach(() =>
  folders
    .splice(0)
    .forEach((directory) =>
      rmSync(directory, { recursive: true, force: true }),
    ),
);
const ids = fixture.entries
  .filter((entry) => /^(stack-|more-|avatar-)/.test(entry.id))
  .map((entry) => entry.id);
const words = (value: string) =>
  value.match(/[\p{L}\p{N}]+(?:[’':.,-][\p{L}\p{N}]+)*/gu) ?? [];

it("renders every reviewed card with its frame, body budget and own access note before leaving", () => {
  const links = reader();
  const banned = new RegExp(
    `\\b(${[...policy.banned, ...policy.jargon].join("|")})\\b`,
    "i",
  );
  for (const id of ids) {
    const preview = links.get(id)!;
    const html = renderToStaticMarkup(<LinkOut preview={preview} />);
    expect(html).toContain('viewBox="0 0 320 152"');
    expect(html).toContain("data-link-frame");
    const body = words([...preview.body, ...preview.properNames].join(" "));
    expect(body.length).toBeLessThanOrEqual(12);
    expect(body.filter((word) => /\d/.test(word)).length).toBeLessThanOrEqual(
      2,
    );
    expect(preview.body.join(" ")).not.toMatch(banned);
    expect(html).not.toMatch(
      /fly\.io\/apps|fly-metrics\.net|dashboard\.secrets\.example|dashboard\.clerk\.com|app\.inngest\.com/,
    );
    if (preview.destination && "repo" in preview.destination) {
      expect(html).toContain('class="link-out code-link"');
      expect(html.indexOf(preview.access)).toBeLessThan(
        html.indexOf('href="https://github.com'),
      );
      expect(linkHref(preview)).toMatch(/\/(blob|tree)\/[a-f0-9]{40}(\/|$)/);
      expect(html).toContain('target="_blank" rel="noopener noreferrer"');
    }
    if (preview.properNames.length) expect(html).toContain("data-proper-name");
  }
});
it("uses blob for files and docs, including the Side door heading", () => {
  const links = reader();
  expect(linkHref(links.get("stack-code-workbench")!)).toMatch(
    /\/blob\/[a-f0-9]{40}\/functions\/src\/mdp_functions\/workbench\.py$/,
  );
  expect(linkHref(links.get("stack-code-clock")!)).toMatch(
    /\/blob\/[a-f0-9]{40}\/ops\/run\.sh$/,
  );
  expect(linkHref(links.get("stack-code-side-door")!)).toMatch(
    /\/blob\/[a-f0-9]{40}\/docs\/analyst-access\.md#connect-a-bi-tool$/,
  );
});
it("keeps a pinned fallback actionable and hides the whole code disclosure without a revision", () => {
  const links = reader({ ...fixture, revision: "a".repeat(40), entries: [] });
  const fallback = links.get("stack-code-warehouse")!;
  const html = renderToStaticMarkup(<LinkOut preview={fallback} />);
  expect(html).toContain("Preview not recorded at this deploy");
  expect(html).toContain("Open code");
  expect(html).not.toContain("<svg");
  expect(
    renderToStaticMarkup(
      <LinkOut preview={{ ...fallback, revision: null, destination: null }} />,
    ),
  ).toBe("");
});
it.each([
  "synthetic.internal",
  "vol_abc123def456",
  "EXAMPLE_SECRET",
  "synthetic-reader@example.invalid",
  "/home/synthetic-reader/project",
  "ghp_" + "x".repeat(36),
])("keeps rejected preview text out of rendered HTML", (sentinel) => {
  const links = reader({
    ...fixture,
    revision: "a".repeat(40),
    entries: fixture.entries.map((entry) =>
      entry.id === "stack-code-warehouse"
        ? { ...entry, body: [sentinel] }
        : entry,
    ),
  });
  expect(
    renderToStaticMarkup(
      <LinkOut preview={links.get("stack-code-warehouse")} />,
    ),
  ).not.toContain(sentinel);
});
it("keeps both components presentational", () => {
  for (const file of ["link-preview.tsx", "link-out.tsx"]) {
    const source = readFileSync(
      new URL(`../components/${file}`, import.meta.url),
      "utf8",
    );
    expect(source).not.toMatch(/use client|server\/|links\.generated/);
  }
});
