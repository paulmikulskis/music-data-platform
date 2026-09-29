import { expect, it, vi } from "vitest";
import postgres from "postgres";
import { randomUUID } from "node:crypto";
vi.mock("server-only", () => ({}));
import { callPlacesSql, currentAnchorsSql } from "../server/call-reads";
const url = process.env.MDP_CALL_TEST_URL ?? "";
// Point the reads at temporary tables with the same columns.
const places = (table: string) =>
  callPlacesSql.replaceAll("marts.mart_shazam_chart_daily", table);
const anchors = (keys: string, titles: string, clusters: string) =>
  currentAnchorsSql
    .replaceAll("explore_intermediate.int_song_key__daily", keys)
    .replaceAll("explore_intermediate.int_song_cluster__daily", clusters)
    .replaceAll("marts.mart_song_day", titles);
it.skipIf(!url)(
  "excludes snapshots and same-day charts before exact counts and the sixty-place bound",
  async () => {
    const db = postgres(url, { max: 1 });
    try {
      await db`CREATE TEMP TABLE call_charts(apple_song_id text,country text,city text,chart_date date,chart text,chart_type text,position integer,source_keys text)`;
      await db`INSERT INTO call_charts SELECT 'apple-a','DE','city-'||lpad(n::text,3,'0'),'2026-09-27','chart-'||n,'city',1,'["sz_chart"]' FROM generate_series(1,70) n`;
      await db`INSERT INTO call_charts SELECT 'apple-b',country,city,chart_date,chart,chart_type,position,source_keys FROM call_charts`;
      await db`INSERT INTO call_charts VALUES ('apple-a','DE',null,'2026-09-27','country-de','country',2,'["sz_chart"]'),('apple-a','FR',null,'2026-09-27','country-fr','country',3,'["sz_chart"]'),('apple-a','US','same-day','2026-09-26','same-day','city',1,'["sz_chart"]'),('apple-a','US','too-late','2026-10-25','too-late','city',1,'["sz_chart"]'),('apple-b','JP','copy-only','2026-09-28','copy-only','city',4,'["sz_chart"]')`;
      const id = randomUUID();
      // apple-b is a matched copy: it reaches the call only through the song group.
      const input = [
        {
          id,
          submitted_at: "2026-09-26T23:30:00Z",
          facts: {
            places_shown: [
              { country: "DE", city: null },
              { country: "DE", city: "city-001" },
            ],
          },
          apple_ids: ["apple-a", "apple-b"],
          grouped_ids: ["apple-b"],
        },
      ];
      const [row] = await db.unsafe(places("call_charts"), [
        JSON.stringify(input),
      ]);
      expect(row.total).toBe("71");
      expect(row.places).toHaveLength(60);
      expect(row.places[0].city).toBe("city-002");
      expect(JSON.stringify(row.places)).not.toMatch(
        /same-day|too-late|city-001/,
      );
      // A place both copies reached keeps the strict observation; one only the copy reached is labeled.
      expect(row.copies).toBe("1");
      expect(row.places[0].matched_copy).toBe(false);
      input[0].facts.places_shown = [];
      const [all] = await db.unsafe(places("call_charts"), [
        JSON.stringify(input),
      ]);
      expect(all.total).toBe("73");
      const [noneShown] = await db.unsafe(places("call_charts"), [
        JSON.stringify([{ ...input[0], facts: { places_shown: null } }]),
      ]);
      expect(noneShown.total).toBe("73");
      const [copyOnly] = await db.unsafe(places("call_charts"), [
        JSON.stringify([
          {
            ...input[0],
            apple_ids: ["apple-b"],
            grouped_ids: ["apple-b"],
            facts: { places_shown: [] },
          },
        ]),
      ]);
      expect(copyOnly.copies).toBe(copyOnly.total);
      expect(
        copyOnly.places.every(
          (p: { matched_copy: boolean }) => p.matched_copy,
        ),
      ).toBe(true);
    } finally {
      await db.end();
    }
  },
);
it.skipIf(!url)(
  "unions frozen Apple anchors, current matches and song-group copies in one batch",
  async () => {
    const db = postgres(url, { max: 1 });
    try {
      await db`CREATE TEMP TABLE call_keys(platform text,platform_track_id text,song_key text)`;
      await db`CREATE TEMP TABLE call_titles(song_key text,day date,title_text text,artist_text text)`;
      await db`CREATE TEMP TABLE call_clusters(song_key text,cluster_key text)`;
      await db`INSERT INTO call_keys VALUES ('spotify','s1','merged'),('apple','a1','merged'),('apple','a2','merged'),('apple','a9','apple-copy'),('spotify','s5','spotify-only'),('spotify','s6','lonely')`;
      // A provisional group joins the Spotify-only key and an Apple copy that identity keeps apart.
      await db`INSERT INTO call_clusters VALUES ('merged','merged'),('apple-copy','merged'),('spotify-only','merged'),('lonely','lonely')`;
      const [corrected, grouped, pending] = [
        randomUUID(),
        randomUUID(),
        randomUUID(),
      ];
      const call = (
        id: string,
        list: { platform: string; platform_track_id: string }[],
        key: string,
      ) => ({
        id,
        song_key: key,
        facts: { anchors: list.map((a) => ({ ...a, song_key: key })) },
      });
      const rows = await db.unsafe(
        anchors("call_keys", "call_titles", "call_clusters"),
        [
          JSON.stringify([
            call(
              corrected,
              [
                { platform: "spotify", platform_track_id: "s1" },
                { platform: "apple", platform_track_id: "a0" },
              ],
              "old",
            ),
            call(
              grouped,
              [{ platform: "spotify", platform_track_id: "s5" }],
              "spotify-only",
            ),
            call(
              pending,
              [{ platform: "spotify", platform_track_id: "s6" }],
              "lonely",
            ),
          ]),
        ],
      );
      const by = (id: string) => rows.find((r) => r.call_id === id);
      expect(by(corrected)?.changed).toBe(true);
      expect(by(corrected)?.apple_ids).toEqual(["a0", "a1", "a2", "a9"]);
      expect(by(corrected)?.grouped_ids).toEqual(["a9"]);
      // Spotify-only, joined by the group: places come from the Apple copies, not "match pending".
      expect(by(grouped)?.changed).toBe(false);
      expect(by(grouped)?.apple_ids).toEqual(["a1", "a2", "a9"]);
      expect(by(grouped)?.grouped_ids).toEqual(["a1", "a2", "a9"]);
      expect(by(pending)?.apple_ids).toEqual([]);
    } finally {
      await db.end();
    }
  },
);

it.skipIf(!url)(
  "bounds a board with fifty calls, one thousand anchors and grouped copies",
  async () => {
    const db = postgres(url, { max: 1 });
    try {
      await db`CREATE TEMP TABLE board_keys(platform text,platform_track_id text,song_key text)`;
      await db`CREATE TEMP TABLE board_titles(song_key text,day date,title_text text,artist_text text)`;
      await db`CREATE TEMP TABLE board_clusters(song_key text,cluster_key text)`;
      await db`CREATE TEMP TABLE board_charts(apple_song_id text,country text,city text,chart_date date,chart text,chart_type text,position integer,source_keys text)`;
      await db`INSERT INTO board_keys SELECT 'apple','track-'||n,'song-'||((n-1)/20) FROM generate_series(1,1000) n`;
      // Pairs of songs share a group, so each call also reaches 20 Apple ids through its pair.
      await db`INSERT INTO board_clusters SELECT 'song-'||n,'song-'||(n/2*2) FROM generate_series(0,49) n`;
      await db`INSERT INTO board_charts SELECT platform_track_id,'DE','city-'||c,'2026-09-27','chart-'||c,'city',1,'["sz_chart"]' FROM board_keys CROSS JOIN generate_series(1,70) c`;
      await db`CREATE INDEX ON board_keys(platform,platform_track_id)`;
      await db`CREATE INDEX ON board_keys(song_key)`;
      await db`CREATE INDEX ON board_clusters(song_key)`;
      await db`CREATE INDEX ON board_clusters(cluster_key)`;
      await db`CREATE INDEX ON board_charts(apple_song_id,chart_date)`;
      await db`ANALYZE board_keys`;
      await db`ANALYZE board_clusters`;
      await db`ANALYZE board_charts`;
      const calls = Array.from({ length: 50 }, (_, i) => ({
        id: randomUUID(),
        song_key: `song-${i}`,
        submitted_at: "2026-09-26T12:00:00Z",
        facts: {
          places_shown: null,
          anchors: Array.from({ length: 20 }, (_, j) => ({
            platform: "apple",
            platform_track_id: `track-${i * 20 + j + 1}`,
            song_key: `song-${i}`,
          })),
        },
      }));
      const start = performance.now();
      const matched = await db.unsafe(
        anchors("board_keys", "board_titles", "board_clusters"),
        [JSON.stringify(calls)],
      );
      expect(matched).toHaveLength(50);
      expect(matched.every((r) => r.apple_ids.length === 40)).toBe(true);
      expect(matched.every((r) => r.grouped_ids.length === 20)).toBe(true);
      const rows = await db.unsafe(places("board_charts"), [
        JSON.stringify(
          calls.map((c) => {
            const m = matched.find((r) => r.call_id === c.id);
            return {
              ...c,
              apple_ids: m?.apple_ids,
              grouped_ids: m?.grouped_ids,
            };
          }),
        ),
      ]);
      expect(rows).toHaveLength(50);
      expect(
        rows.every(
          (r) =>
            r.total === "70" && r.copies === "0" && r.places.length === 60,
        ),
      ).toBe(true);
      expect(performance.now() - start).toBeLessThan(5000);
    } finally {
      await db.end();
    }
  },
);

it.skipIf(!url)(
  "uses the previous 28 days only when the saved places are unknown",
  async () => {
    const db = postgres(url, { max: 1 });
    try {
      await db`CREATE TEMP TABLE prior_charts(apple_song_id text,country text,city text,chart_date date,chart text,chart_type text,position integer,source_keys text)`;
      await db`INSERT INTO prior_charts VALUES
      ('a','DE',null,'2026-08-29','de','country',1,'["sz_chart"]'),
      ('copy','FR','known','2026-09-26','fr','city',1,'["sz_chart"]'),
      ('a','JP','old','2026-08-28','jp','city',1,'["sz_chart"]'),
      ('unrelated','US','new','2026-09-25','us','city',1,'["sz_chart"]')`;
      await db`INSERT INTO prior_charts SELECT 'a',country,city,'2026-09-27',chart,chart_type,position,source_keys FROM prior_charts`;
      const call = {
        id: randomUUID(),
        submitted_at: "2026-09-26T00:30:00Z",
        apple_ids: ["a", "copy"],
        grouped_ids: ["copy"],
      };
      const read = async (
        shown: { country: string; city: string | null }[] | null,
      ) => {
        const [row] = await db.unsafe(places("prior_charts"), [
          JSON.stringify([{ ...call, facts: { places_shown: shown } }]),
        ]);
        return row;
      };
      const unknown = await read(null);
      expect(unknown.total).toBe("2");
      expect(unknown.places.map((p: { city: string }) => p.city)).toEqual([
        "old",
        "new",
      ]);
      expect((await read([])).total).toBe("4");
      expect((await read([{ country: "JP", city: "old" }])).total).toBe(
        "3",
      );
    } finally {
      await db.end();
    }
  },
);
