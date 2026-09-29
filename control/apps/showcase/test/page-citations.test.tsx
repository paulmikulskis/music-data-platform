import type { ReactNode } from "react";
import { existsSync } from "node:fs";
import { beforeEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  metadata_mart_song_day,
  metadata_mart_top_movers_current,
} from "@mdp/data-sdk";
import { build, days, holdings, movers } from "./browser/fixtures";
import { mover } from "../server/models";
import type { Provenance } from "../components/number";
import { martCitationSql } from "../server/citation-sql";

const state = vi.hoisted(() => ({ unavailable: false, empty: false }));
vi.mock("server-only", () => ({}));
vi.mock("../components/home/cover-strip", () => ({ CoverStrip: () => null }));
vi.mock("../components/activity", () => ({ Activity: () => null }));
vi.mock("../server/room", () => ({
  room: async () => ({ person: {}, csrf_token: "fixture" }),
  unavailable: () => null,
}));
vi.mock("../server/call-offers", () => ({
  callOffers: async () => ({}),
  songProjection: () => ({}),
  moverProjection: () => ({}),
}));
vi.mock("../server/call-reads", () => ({ callsBoard: async () => null }));
vi.mock("../server/visits", () => ({ visitChanged: async () => false }));
vi.mock("../server/proof-store", () => ({ capturedProof: () => null }));
vi.mock("../server/platform", () => ({
  tending: async () => ({ sources: [], songs: null }),
  holdingsSummary: async () => ({ value: holdings, state: "live" }),
  holdings: async () => ({ value: holdings, state: "live" }),
}));
vi.mock("../components/shell", () => ({
  Shell: ({
    children,
    provenances,
  }: {
    children: ReactNode;
    provenances: (Provenance | undefined)[];
  }) => (
    <>
      {provenances.map((p, i) => (
        <pre key={i}>{p?.sql}</pre>
      ))}
      {children}
    </>
  ),
}));
vi.mock("../components/song", () => ({ Song: () => <p>Song history</p> }));
vi.mock("../components/holdings", () => ({ Holdings: () => <p>Holdings</p> }));
vi.mock("../server/reads", async (original) => {
  const actual = await original<typeof import("../server/reads")>();
  return {
    ...actual,
    alias: async () => ({ value: { rows: [] } }),
    movers: async () => ({
      value: {
        rows: movers.map((row) => mover.parse(row)),
        build: build("mart_top_movers_current"),
        queried_at: holdings.queried_at,
        sql: martCitationSql(metadata_mart_top_movers_current, {
          limit: 3,
          filters: { movement_list: "new_entries" },
        }),
      },
    }),
    songMovement: async () => ({ value: { rows: [] } }),
    songHistory: async () =>
      state.unavailable
        ? null
        : {
            value: {
              rows: state.empty ? [] : days,
              build: build("mart_song_day"),
              queried_at: holdings.queried_at,
              sql: martCitationSql(metadata_mart_song_day, {
                limit: 100,
                filters: { song_key: "apple:test" },
                range: { day: { from: "2026-08-30", to: "2026-09-27" } },
              }),
            },
          },
    identities: async () => null,
    movementReadiness: async () => null,
    songPlaces: async () => null,
    trackedSongs: async () => null,
    rights: async () => ({
      value: {
        rows: [],
        build: build("catalog.learning_rights"),
        queried_at: holdings.queried_at,
        sql: "SELECT source_key, learning_eligible, resale_permitted FROM catalog.learning_rights",
      },
    }),
  };
});
import Today from "../app/page";
import SongPage from "../app/s/song/[key]/page";
import HoldingsPage from "../app/holdings/page";
const songPage = () =>
  SongPage({
    params: Promise.resolve({ key: "apple:test" }),
    searchParams: Promise.resolve({}),
  });
beforeEach(() => {
  state.unavailable = false;
  state.empty = false;
});
it("Home keeps the mover citation without a collection counter", async () => {
  const html = renderToStaticMarkup(await Today());
  for (const column of metadata_mart_top_movers_current.columns)
    expect(html).toContain(column);
  expect(html).not.toContain("platform holdings");
});
it("Song renders the selected history columns and its read dates", async () => {
  const html = renderToStaticMarkup(await songPage());
  for (const column of metadata_mart_song_day.columns)
    expect(html).toContain(column);
  expect(html).toContain("2026-08-30");
  expect(html).toContain("2026-09-27");
  expect(html).toContain("LIMIT 100");
});
it("Holdings copies the CLI for control data and readable rights columns", async () => {
  const html = renderToStaticMarkup(
    await HoldingsPage({ searchParams: Promise.resolve({}) }),
  );
  expect(html).toContain(
    `pnpm --dir control mdp platform holdings --since ${holdings.since}`,
  );
  for (const column of ["source_key", "learning_eligible", "resale_permitted"])
    expect(html).toContain(column);
  expect(html).toContain("catalog.learning_rights");
  expect(html).not.toContain("control.load");
  expect(html).not.toContain("review_required");
});
it("offers Retry for an unavailable song read", async () => {
  state.unavailable = true;
  const html = renderToStaticMarkup(await songPage());
  expect(html).toContain('href="/s/song/apple%3Atest"');
  expect(html).toContain("Retry");
  expect(html).not.toContain("song not found");
});
it("opens Library search for an unknown song after successful reads", async () => {
  state.empty = true;
  const html = renderToStaticMarkup(await songPage());
  expect(html).toContain("song not found");
  expect(html).toContain('href="/search"');
  expect(html).toContain("Search the Library");
  expect(html).toContain("No song with this key.");
  expect(existsSync(new URL("../app/_rising/page.tsx", import.meta.url))).toBe(
    true,
  );
});
