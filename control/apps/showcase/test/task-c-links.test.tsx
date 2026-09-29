import { afterAll, expect, it, vi } from "vitest";
import { rmSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
vi.mock("server-only", () => ({}));
import { linkFixture } from "./link-fixture";
import fixture from "./fixtures/links.json";
import { readLinks } from "../server/links";
import { linkHref } from "../lib/links";
import { LinkOut } from "../components/link-out";
import { StepLinks, SqlLayers } from "../components/team/links";
import { TeamPanel } from "../components/home/team-panel";
import { MarkCredits } from "../components/stack/credits";
import { appDescriptions } from "../lib/apps";
import { analystSteps } from "../lib/analyst-steps";
import {
  LoginRequest,
  loginRequestLine,
} from "../components/team/login-request";
const directory = linkFixture();
afterAll(() => rmSync(directory, { recursive: true, force: true }));
const reader = readLinks(directory);
const previews = Object.fromEntries(
  fixture.entries.map((entry) => [
    entry.id,
    reader.get(entry.id, entry.variant),
  ]),
);

it("starts with the contributing guide and makes every analyst destination reachable", () => {
  const html = renderToStaticMarkup(
    <>
      {analystSteps.map((step, index) => (
        <StepLinks key={step.id} index={index} links={previews} />
      ))}
      <SqlLayers links={previews} />
    </>,
  );
  expect(html.indexOf("team-1-contributing")).toBeLessThan(
    html.indexOf("More for analysts"),
  );
  expect(html.match(/More for analysts/g)).toHaveLength(5);
  for (const entry of fixture.entries.filter((entry) =>
    /^team-(?:[1-5]-|layer-)/.test(entry.id),
  )) {
    expect(html).toContain(`data-link-id="${entry.id}"`);
  }
  for (const folder of [
    "staging",
    "intermediate",
    "marts/global",
    "marts/global",
  ]) {
    expect(html).toContain(`dbt/models/${folder}`);
  }
  expect(analystSteps[4]?.text).toBe(
    "Read how reviewed models become served tables.",
  );

  for (const step of analystSteps) {
    for (const field of Object.values(step)) {
      expect(field).not.toMatch(/\bpatch\b/i);
    }
  }
  expect(html).not.toContain("/tree/main");
});
it("renders the login request with copy and an addressless mailto", () => {
  const html = renderToStaticMarkup(<LoginRequest />);
  expect(html).toContain(renderToStaticMarkup(<code>{loginRequestLine}</code>));
  expect(html).toContain("Copy request");
  expect(html).toContain('href="mailto:?subject=Analyst%20login&amp;body=');
  const mailto = html.match(/href="(mailto:[^"]*)"/)?.[1];
  expect(mailto).toBeDefined();
  expect(mailto).not.toContain("@");
});
it("pins every new code disclosure and keeps every access note beside its link", () => {
  for (const entry of fixture.entries.filter((entry) =>
    /^(team-|viewer-|sources-|proof-)/.test(entry.id),
  )) {
    const preview = reader.get(entry.id, entry.variant);
    const html = renderToStaticMarkup(<LinkOut preview={preview} />);
    if (!preview?.destination) continue;
    if ("repo" in preview.destination) {
      expect(linkHref(preview)).toMatch(/\/(blob|tree)\/[a-f0-9]{40}\//);
      expect(html).toContain(
        "Public source. GitHub shows contributor handles.",
      );
      expect(html.indexOf('class="link-access"')).toBeLessThan(
        html.indexOf('href="https://github.com'),
      );
    } else if (preview.kind === "page") {
      expect(html).toContain("Drawing, not the live page");
      expect(html).toContain("Not checked");
    }
  }
});
it("draws folder counts after names and keeps doc text readable", () => {
  const folder = reader.get("team-3-r-package")!;
  const html = renderToStaticMarkup(<LinkOut preview={folder} />);
  expect(html.indexOf("+120 more")).toBeGreaterThan(html.indexOf(">tests<"));
  const doc = {
    ...reader.get("team-1-contributing")!,
    properNames: ["A long guide heading for a first SQL contribution"],
  };
  const drawing = renderToStaticMarkup(<LinkOut preview={doc} />);
  expect(drawing).toContain('font-size="14"');
  expect(drawing.match(/<tspan/g)?.length).toBeGreaterThan(2);
});
it("uses real Team anchors and in-app mark credits", () => {
  const html = renderToStaticMarkup(
    <>
      <TeamPanel />
      <MarkCredits />
    </>,
  );
  expect(html.match(/href="\/team#first-question"/g)).toHaveLength(3);
  expect(html).toContain('href="/team#screen"');
  expect(html).toContain('href="/sources?credits=1"');
  expect(html).not.toContain("LICENSES.md");
  for (const app of appDescriptions) {
    const words = app.tagline.split(/\s+/);
    expect(words.length).toBeGreaterThanOrEqual(4);
    expect(words.length).toBeLessThanOrEqual(8);
    expect(app.tagline).toMatch(/Music Data Platform|the platform/);
  }
});

it("drops rejected preview content on Team, Sources and viewer code cards", async () => {
  const { forbidden } = await import("./browser/link-privacy");
  const ids = [
    "team-1-contributing",
    "sources-reader-code",
    "viewer-node-code",
  ];
  const injected = linkFixture({
    ...fixture,
    revision: "a".repeat(40),
    entries: fixture.entries.map((entry) =>
      ids.includes(entry.id) ? { ...entry, body: forbidden } : entry,
    ),
  });
  try {
    const links = readLinks(injected);
    for (const id of ids) {
      const entry = fixture.entries.find((entry) => entry.id === id)!;
      const preview = links.get(id, entry.variant);
      const html = renderToStaticMarkup(<LinkOut preview={preview} />);
      expect(preview?.available).toBe(false);
      expect(html).toContain("Preview not recorded at this deploy");
      for (const sentinel of forbidden) expect(html).not.toContain(sentinel);
    }
  } finally {
    rmSync(injected, { recursive: true, force: true });
  }
});
