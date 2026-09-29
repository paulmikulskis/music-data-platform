import { afterAll, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
vi.mock("server-only", () => ({}));
import { sensitiveStrings, stackSchema, type Stack } from "../lib/stack";
import { datedFacts, stackAliases } from "../lib/stack-facts";
import { stackCards, plannedStack } from "../lib/stack-cards";
import { stackView as resolveStackView } from "../server/stack";
import { StackCards } from "../components/stack/cards";
import { StackOverview } from "../components/stack/overview";
import { MarkCredits } from "../components/stack/credits";
import { Apps } from "../components/apps";
import { platformLine } from "../lib/apps";
import { TeamDesk } from "../components/team/desk";
import { analystSteps } from "../lib/analyst-steps";
import { lineage } from "../lib/lineage";

import { readLinks } from "../server/links";
import { linkHref } from "../lib/links";
import { linkFixture } from "./link-fixture";
import { rmSync } from "node:fs";
const linkDirectory = linkFixture();
afterAll(() => rmSync(linkDirectory, { recursive: true, force: true }));
const stackView = (load: Parameters<typeof resolveStackView>[0]) =>
  resolveStackView(load, readLinks(linkDirectory));
const revision = "a".repeat(40);
const captured = "2026-09-27T12:00:00Z";
function artifact(overrides: Partial<Stack["services"][number]> = {}): Stack {
  return stackSchema.parse({
    schema_version: 1,
    input_hashes: {},
    captured_at: captured,
    services: stackAliases.map((alias) => {
      const card = stackCards.find((item) => item.alias === alias)!;
      return {
        alias,
        kind: card.kind,
        process_role: "role",
        region: "New Jersey",
        machine_count: alias === "The clock" ? 3 : 1,
        cpu_total: 2,
        memory_mb_total: 2048,
        storage_gb_total: alias === "Warehouse" ? 20 : 0,
        encrypted: alias === "Warehouse" ? true : null,
        measured_at: captured,
        public_link: null,
        status: { state: "not_checked", name: "Not checked", checked_at: null },
        facts: datedFacts[alias] ?? [],
        ...(card.alias === overrides.alias ? overrides : {}),
      };
    }),
  });
}
const loaded = (stack: Stack) => () => ({
  stack,
  build: {
    schema_version: 1 as const,
    revision,
    lineage_hash: "b".repeat(64),
    stack_hash: "c".repeat(64),
    links_hash: "d".repeat(64),
    lineage_inputs: {},
    stack_inputs: {},
    links_inputs: {},
  },
  lineage,
});
// A private host, an address, a machine id, a volume id and name, an image reference, a key,
// a secret name, a connection string, and unlisted consoles and databases.
const sentinels = [
  "10.20.30.40",
  "fdaa:0:1234:a7b:1f2:3c4d:5e6f:2",
  "mdp-postgres.internal",
  "e784e9d4b12345",
  "vol_abc123def456",
  "pgdata",
  "mb_data",
  "registry.fly.io/mdp-showcase:deployment-01ABC",
  "sk_" + "live_" + "abcdefghijklmnop",
  "ANTHROPIC_API_KEY",
  "CLERK_SECRET_KEY",
  "DATABASE_URL",
  "postgresql://user:secret@host/db",
];

describe("stack view", () => {
  it("renders measured counts, the deployed revision and dated facts from a valid artifact", () => {
    const view = stackView(loaded(artifact()));
    expect(view.mode).toBe("generated");
    expect(view.notice).toBeNull();
    expect(view.revision).toBe(revision);
    const clock = view.services.find((s) => s.alias === "The clock")!;
    expect(clock.count).toEqual({
      text: "3 servers on a timer",
      as_of: captured,
      measured: true,
    });
    const warehouse = view.services.find((s) => s.alias === "Warehouse")!;
    expect(warehouse.storage?.text).toBe("20 GB encrypted disk");
    expect(warehouse.facts.some((f) => /disk/.test(f.text))).toBe(false);
    expect(warehouse.code && linkHref(warehouse.code)).toBe(
      `https://github.com/paulmikulskis/music-data-platform/tree/${revision}/ops/fly/postgres`,
    );
    // The avatar's shape is the measured machine list by kind, with the artifact's date.
    expect(view.shape).toEqual({
      databases: 2,
      servers: 6,
      timers: 3,
      as_of: captured,
    });
  });
  it("carries no shape when any platform service is unmeasured", () => {
    const stack = artifact();
    const clock = stack.services.find((s) => s.alias === "The clock")!;
    clock.machine_count = null;
    expect(stackView(loaded(stack)).shape).toBeNull();
  });
  it("leaves counts unmeasured with a notice when the artifact is missing", () => {
    const view = stackView(() => {
      throw new Error("ENOENT");
    });
    expect(view.mode).toBe("dated");
    expect(view.notice).toBe("Counts not refreshed at this deploy.");
    expect(view.revision).toBeNull();
    const warehouse = view.services.find((s) => s.alias === "Warehouse")!;
    expect(warehouse.count).toBeNull();
    // Link previews have their own envelope and remain independent of Stack counts.
    expect(warehouse.code?.revision).toBe(revision);
    expect(warehouse.facts.map((f) => f.text)).toContain("PostgreSQL 17.");
    expect(view.services).toHaveLength(stackCards.length);
    expect(view.shape).toBeNull();
  });
  it("refuses an artifact that carries a private host, address, identifier or secret", () => {
    for (const sentinel of sentinels) {
      const view = stackView(
        loaded(
          artifact({
            alias: "Readers",
            facts: [{ text: sentinel, captured_at: captured, evidence: "x" }],
          }),
        ),
      );
      expect(view.mode).toBe("dated");
      expect(JSON.stringify(view)).not.toContain(sentinel);
      const html = renderToStaticMarkup(<StackCards view={view} />);
      expect(html).not.toContain(sentinel);
    }
    expect(sensitiveStrings({ text: "PostgreSQL 17, 597 MB, 27 Sep" })).toEqual(
      [],
    );
    expect(sensitiveStrings(datedFacts)).toEqual([]);
    expect(sensitiveStrings({ nested: [{ note: "fdaa:0:1::2" }] })).toEqual([
      "$.nested[0].note",
    ]);
  });
  it("names every service in the artifact and no leftover or unlisted host", () => {
    expect(stackAliases.sort()).toEqual(stackCards.map((c) => c.alias).sort());
    const text = JSON.stringify([stackCards, plannedStack, datedFacts]);
    expect(text).not.toMatch(/mdp-rebuild|-analyst-a|-analyst-b|\$\d|\bcost\b/i);
  });
  it("pins reviewed code and removes links without a revision", () => {
    const view = stackView(loaded(artifact()));
    const html = renderToStaticMarkup(<StackCards view={view} />);
    expect(html).toContain("Code at this page&#x27;s build · rev aaaaaaa");
    expect(html).toContain(
      "Public source. GitHub shows contributor handles.",
    );
    const dated = resolveStackView(
      loaded(artifact()),
      readLinks("/missing-link-fixture"),
    );
    expect(renderToStaticMarkup(<StackCards view={dated} />)).not.toContain(
      "github.com/paulmikulskis",
    );
    expect(JSON.stringify(view)).not.toContain("tree/main");
  });
  it("keeps each fact's own capture date on the facts line", () => {
    const html = renderToStaticMarkup(
      <StackCards view={stackView(loaded(artifact()))} />,
    );
    const card = (id: string) =>
      html.slice(
        html.indexOf(`id="${id}"`),
        html.indexOf("</article>", html.indexOf(`id="${id}"`)),
      );
    // The MusicBrainz import (23 Sept) and disk measurement (24 Sept) keep their dates.
    const brainz = card("musicbrainz");
    expect(brainz).toContain(
      "12 synthetic records at import<small> (23 Sept)</small>",
    );
    expect(brainz).toContain(
      "1 MB in use on a 1 GB example disk<small> (24 Sept)</small>",
    );
    expect(brainz).not.toContain("as of 27 Sept");
    // Facts that share one date close the line with it once.
    const warehouse = card("warehouse");
    expect(warehouse).toContain("as of 27 Sept");
    expect(warehouse).not.toContain("(27 Sept)");
  });
});

describe("stack page pieces", () => {
  it("renders grouped cards with kind, name, dated facts, access text and a hollow status before any check", () => {
    const view = stackView(loaded(artifact()));
    const html = renderToStaticMarkup(<StackCards view={view} />);
    expect(html).toContain('id="music-data-platform"');
    expect(html).toContain('id="planned"');
    expect(html).toContain('id="warehouse"');
    expect(html).toContain(
      "Public source. GitHub shows contributor handles.",
    );
    expect(html).toContain("Code");
    expect(html).toContain('class="status-dot unknown"');
    expect(html).toContain("Installed, no tables yet");
    expect(html).not.toContain("Counts not refreshed");
    expect(html).not.toMatch(/\bCTO\b|\$[0-9]/);
  });
  it("shows the missing-artifact notice on every card in dated mode", () => {
    const view = stackView(() => {
      throw new Error("missing");
    });
    const html = renderToStaticMarkup(<StackCards view={view} />);
    expect(html.match(/Counts not refreshed at this deploy\./g)?.length).toBe(
      stackCards.length,
    );
  });
  it("draws the overview with one node per platform service and no dotted background", () => {
    const html = renderToStaticMarkup(<StackOverview />);
    for (const id of [
      "clock",
      "readers",
      "warehouse",
      "data-api",
      "musicbrainz",
      "this-app",
    ])
      expect(html).toContain(`href="#${id}"`);
    expect(html).toContain('aria-label="Open Spotify"');
    expect(html).toContain('aria-label="Open Fly.io"');
    expect(html).toContain('href="https://fly.io"');
    expect(html).toContain('stroke-dasharray="4 4"');
    expect(html).not.toMatch(/pattern|<image[^>]*grid/);
    expect(html).toContain('data-state="idle"');
  });
  it("carries the trademark and license lines and the analyst desk marks", () => {
    const credits = renderToStaticMarkup(<MarkCredits />);
    expect(credits).toContain("dbt Labs, LLC");
    expect(credits).toContain("PostgreSQL Community Association of Canada");
    expect(credits).toContain("CC BY-SA 4.0");
    const desk = renderToStaticMarkup(
      <TeamDesk
        links={{
          cylinder: readLinks(linkDirectory).get(
            "team-desk-anchors",
            "cylinder",
          ),
          laptop: readLinks(linkDirectory).get("team-desk-anchors", "laptop"),
          screen: readLinks(linkDirectory).get("team-desk-anchors", "screen"),
        }}
      />,
    );
    for (const label of ["PostgreSQL", "R", "Python", "dbt"])
      expect(desk).toContain(`aria-label="Open ${label}"`);
    expect(desk).toContain("DuckDB, RStudio, DBeaver or Jupyter");
    expect(analystSteps).toHaveLength(5);

  });
  it("keeps the stack next step while the avatar links load", () => {
    const html = renderToStaticMarkup(<Apps compact />);
    expect(html).not.toContain("See more →");
    // No count line until a measured, dated shape arrives from /s/apps.
    expect(html).not.toMatch(/on a timer/);
    expect(html).toContain('href="/stack"');
    expect(
      platformLine({ databases: 2, servers: 6, timers: 3, as_of: captured }),
    ).toBe("2 databases · 6 servers · 3 on a timer · counted 27 Sept");
  });
});
