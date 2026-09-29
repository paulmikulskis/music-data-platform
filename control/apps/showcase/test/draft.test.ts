import { afterAll, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { createHash, randomUUID } from "node:crypto";
import postgres from "postgres";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import type { Session } from "@mdp/showcase-auth";
vi.mock("server-only", () => ({}));
import {
  ruleValues,
  ruleCondition,
  draftReadDay,
  type DraftCandidate,
} from "../lib/draft";
import inventory from "../../../../ops/showcase/queries.json";
import * as clients from "../server/clients";
import * as callReads from "../server/call-reads";
import { ensureDraft } from "../server/draft-reads";
import { budget } from "../server/read-budget";
import { callWeek } from "../lib/calls";
import {
  freezeDraft as freezeDraftAt,
  backRule,
  closeDraft,
  readDraft,
  draftCloser,
} from "../server/draft-store";
import { closeDueDrafts, scheduleDraft } from "../server/draft-job";
import { submitCall, lock } from "../server/call-store";
import { signCall } from "../server/call-token";
// Freeze fixtures during their own open week, independent of the machine clock.
const freezeDraft = (
  database: postgres.Sql,
  day: string,
  candidates: DraftCandidate[],
) =>
  freezeDraftAt(database, day, candidates, () => new Date(`${day}T12:00:00Z`));
const url = process.env.MDP_CALL_TEST_URL;
let db: ReturnType<typeof postgres> | undefined;
let admin: ReturnType<typeof postgres> | undefined;
const name = `draft_test_${Date.now()}`;
const week = callWeek();
const current: Session = {
  id_hash: "fixture",
  handle: "draft-fixture",
  csrf_token: "fixture",
  person: {
    handle: "draft-fixture",
    display_name: "Test viewer",
    email: "fixture@example.invalid",
    api_key_id: randomUUID(),
    admin_key: "fixture",
  },
};
function candidate(song: string = randomUUID()): DraftCandidate {
  return {
    song_key: song,
    artist_stage: "emerging",
    age_class: "new",
    discovery_entries: 3,
    market_count: 3,
    entered_lists: 2,
    list_reach_tier: 2,
    movement_list: "new_entries",
    rank: 1,
    snapshot: {
      v: 1,
      card: "arrival",
      song_key: song,
      anchors: [{ platform: "apple", platform_track_id: song, song_key: song }],
      anchors_truncated: false,
      places_shown: null,
      builds: [
        {
          relation: "marts.mart_arrivals_current",
          scope: "global",
          tenant_slug: null,
          stamped: true,
          cycle_id: randomUUID(),
          close_no: "68",
          built_at: new Date().toISOString(),
        },
      ],
      source_keys: ["sz_chart"],
      facts_day: draftReadDay(week),
      close_no: "68",
      idempotency_key: randomUUID(),
      handle: current.handle,
      exp: Date.now() + 600000,
      draft_week: week,
      list: "new_entries",
      window_days: 7,
      entered_lists: "2",
      discovery: [],
    },
  };
}
beforeAll(async () => {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  if (!url) return;
  const base = new URL(url);
  expect(["localhost", "127.0.0.1"]).toContain(base.hostname);
  base.pathname = "/postgres";
  admin = postgres(base.toString(), { onnotice: () => {} });
  await admin.unsafe(`CREATE DATABASE ${name} OWNER migrator`);
  base.pathname = `/${name}`;
  db = postgres(base.toString(), { max: 8, onnotice: () => {} });
  base.username = "migrator";
  base.password = "migrator";
  execFileSync("pnpm", ["migrate"], {
    cwd: fileURLToPath(
      new URL("../../../packages/control-db", import.meta.url),
    ),
    env: { ...process.env, MDP_CONTROL_DATABASE_URL: base.toString() },
    stdio: "pipe",
  });
});
beforeEach(async () => {
  if (db)
    await db`TRUNCATE control.showcase_call_rule,control.showcase_rule,control.showcase_call,control.showcase_draft,control.audit_log CASCADE`;
});
afterAll(async () => {
  await db?.end();
  if (admin) {
    await admin.unsafe(`DROP DATABASE ${name} WITH (FORCE)`);
    await admin.end();
  }
  vi.unstubAllEnvs();
});
it("accepts only the fixed vocabulary and bounded values", () => {
  for (const conditions of [
    [{ field: "sql", value: "select 1" }],
    [{ field: "discovery_entries_at_least", value: 10 }],
    Array.from({ length: 5 }, () => ({ field: "age_class", value: "new" })),
    [{ field: "artist_stage", value: "unknown" }],
    [{ field: "markets_at_least", value: 0 }],
  ])
    expect(
      ruleValues.safeParse({ conditions, limit_per_draft: 2 }).success,
    ).toBe(false);
  expect(
    ruleValues.safeParse({
      conditions: [{ field: "reach_tier_at_most", value: 4 }],
      limit_per_draft: 6,
    }).success,
  ).toBe(false);
});
it.skipIf(!url)(
  "freezes the tray once and leaves example rules unbacked",
  async () => {
    if (!db) return;
    const first = await freezeDraft(db, week, [candidate()]);
    expect(await freezeDraft(db, week, [candidate()])).toEqual(first);
    expect(await db`SELECT backed_by FROM control.showcase_rule`).toEqual([
      { backed_by: null },
      { backed_by: null },
    ]);
  },
);
it.skipIf(!url)(
  "two concurrent closes produce one set; two rules retain both ids on one song",
  async () => {
    if (!db) return;
    await freezeDraft(db, week, [candidate()]);
    await backRule(db, current, week, "new-discovery");
    await backRule(db, current, week, "new-lists");
    const [a, b] = await Promise.all([
      closeDraft(db, week, "one", "scheduler"),
      closeDraft(db, week, "two", "scheduler"),
    ]);
    expect(a).toEqual(b);
    expect(await closeDraft(db, week, a.close_key!, "scheduler")).toEqual(a);
    expect(await db`SELECT author FROM control.showcase_call`).toEqual([
      { author: "rules" },
    ]);
    const [saved] =
      await db`SELECT idempotency_key,facts FROM control.showcase_call`;
    expect(saved.facts.idempotency_key).toBe(saved.idempotency_key);
    expect(
      await db`SELECT rule_id FROM control.showcase_call_rule ORDER BY rule_id`,
    ).toEqual([{ rule_id: "new-discovery" }, { rule_id: "new-lists" }]);
    expect(
      await db`SELECT actor FROM control.audit_log WHERE action='showcase.draft_close'`,
    ).toEqual([{ actor: "scheduler" }]);
  },
);
it.skipIf(!url)(
  "a draft call racing close lands before it or is refused, never after",
  async () => {
    if (!db) return;
    const song = candidate();
    await freezeDraft(db, week, [song]);
    const answers = await Promise.allSettled([
      submitCall(db, current, signCall(song.snapshot)),
      closeDraft(db, week, "race", "scheduler"),
    ]);
    const [counts] =
      await db`SELECT count(*)::int AS n FROM control.showcase_call c JOIN control.showcase_draft d ON c.draft_week=d.week_start WHERE c.submitted_at>d.closed_at`;
    expect(counts.n).toBe(0);
    expect(answers[1].status).toBe("fulfilled");
    if (answers[0].status === "rejected")
      expect(answers[0].reason).toMatchObject({ code: "draft_closed" });
  },
);
it.skipIf(!url)(
  "closed drafts refuse fresh calls and backing; an accepted retry still returns its call",
  async () => {
    if (!db) return;
    const song = candidate();
    await freezeDraft(db, week, [song]);
    const signed = signCall(song.snapshot);
    const call = await submitCall(db, current, signed);
    await closeDraft(db, week, "closed", "scheduler");
    await expect(
      submitCall(db, current, signCall(candidate().snapshot)),
    ).rejects.toMatchObject({ code: "draft_closed" });
    await expect(
      backRule(db, current, week, "new-lists"),
    ).rejects.toMatchObject({ code: "draft_closed" });
    expect(await submitCall(db, current, signed)).toEqual(call);
  },
);
it.skipIf(!url)(
  "draft calls share the ordinary five-call author lock",
  async () => {
    if (!db) return;
    const one = candidate();
    const two = candidate();
    await freezeDraft(db, week, [one, two]);
    for (let i = 0; i < 4; i++) {
      const song = candidate();
      delete song.snapshot.draft_week;
      await submitCall(db, current, signCall(song.snapshot));
    }
    const results = await Promise.allSettled([
      submitCall(db, current, signCall(one.snapshot)),
      submitCall(db, current, signCall(two.snapshot)),
    ]);
    expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(1);
    expect(results.filter((r) => r.status === "rejected")).toHaveLength(1);
  },
);
it.skipIf(!url)(
  "validates stored rule values before evaluation and rolls back failed closes",
  async () => {
    if (!db) return;
    await freezeDraft(db, week, [candidate()]);
    await backRule(db, current, week, "new-lists");
    await db`UPDATE control.showcase_rule SET conditions='[{"field":"lists_at_least","value":999}]' WHERE id='new-lists'`;
    await expect(
      closeDraft(db, week, "invalid", "scheduler"),
    ).rejects.toThrow();
    expect((await readDraft(db, week))?.closed_at).toBeNull();
    expect(await db`SELECT id FROM control.showcase_call`).toHaveLength(0);
  },
);
it.skipIf(!url)(
  "uses Eastern 18:00 in summer and winter; a missed timer close is recoverable",
  async () => {
    if (!db) return;
    for (const [day, hour] of [
      ["2026-03-06", 23],
      ["2026-03-13", 22],
      ["2026-10-30", 22],
      ["2026-11-06", 23],
      ["2026-07-03", 22],
    ] as const) {
      await freezeDraft(db, day, []);
      const [row] =
        await db`SELECT extract(hour from closes_at AT TIME ZONE 'UTC')::int AS hour FROM control.showcase_draft WHERE week_start=${day}`;
      expect(row.hour).toBe(hour);
      const saved = await readDraft(db, day);
      expect(saved?.closes_at.slice(0, 10)).toBe(draftReadDay(day));
      expect(callWeek(new Date(saved!.closes_at))).toBe(day);
      expect(callWeek(new Date(`${draftReadDay(day)}T02:54:00Z`))).toBe(day);
    }
    await closeDueDrafts(db);
    expect((await readDraft(db, "2026-07-03"))?.closed_by).toBe("scheduler");
    await freezeDraft(db, week, []);
    const closed = await closeDraft(
      db,
      week,
      "button-after-miss",
      `api-key:${current.person.api_key_id}`,
    );
    expect(closed.closed_by).toBe(`api-key:${current.person.api_key_id}`);
  },
);
it.skipIf(!url)(
  "serializes backing behind the draft lock and rechecks the committed close",
  async () => {
    if (!db) return;
    const database = db;
    await freezeDraft(database, week, []);
    let started: (() => void) | undefined;
    const waiting = new Promise<void>((resolve) => {
      started = resolve;
    });
    const blocker = database.begin(async (tx) => {
      await lock(tx, `showcase:draft:${week}`);
      started?.();
      await tx`SELECT pg_sleep(0.1)`;
      await tx`UPDATE control.showcase_draft SET closed_at=now(),close_key='held' WHERE week_start=${week}`;
    });
    await waiting;
    const back = backRule(database, current, week, "new-lists");
    await blocker;
    await expect(back).rejects.toMatchObject({ code: "draft_closed" });
  },
);
it("runs the timer at startup and each minute, without overlapping ticks", async () => {
  vi.useFakeTimers();
  const tick = vi.fn(async () => {});
  const stop = scheduleDraft(tick);
  await vi.advanceTimersByTimeAsync(120000);
  expect(tick).toHaveBeenCalledTimes(3);
  stop();
  await vi.advanceTimersByTimeAsync(60000);
  expect(tick).toHaveBeenCalledTimes(3);
  vi.useRealTimers();
});

it.skipIf(!url)(
  "rules use frozen values, limits and a fixed list/rank/hash order; no match makes no call",
  async () => {
    if (!db) return;
    const first = candidate("first");
    const second = candidate("second");
    const last = candidate("last");
    first.rank = 1;
    second.rank = 2;
    last.rank = 3;
    for (const c of [first, second, last]) c.entered_lists = 0;
    await freezeDraft(db, week, [last, second, first]);
    await backRule(db, current, week, "new-discovery");
    await backRule(db, current, week, "new-lists");
    await closeDraft(db, week, "ordered", "scheduler");
    expect(
      await db`SELECT song_key FROM control.showcase_call ORDER BY song_key`,
    ).toEqual([{ song_key: "first" }, { song_key: "second" }]);
    expect(
      await db`SELECT DISTINCT rule_id FROM control.showcase_call_rule`,
    ).toEqual([{ rule_id: "new-discovery" }]);
    expect((await readDraft(db, week))?.rules).toHaveLength(2);
  },
);
it.skipIf(!url)(
  "Saturday qualification excludes a straddling interval and non-Discovery charts",
  async () => {
    if (!db) return;
    const day = draftReadDay(week);
    const { traySql } = await import("../server/draft-reads");
    await db.unsafe(`CREATE SCHEMA marts; CREATE SCHEMA explore_intermediate;
    CREATE TABLE marts.mart_arrivals_current(song_key text,cluster_key text,member_song_keys text,artist_stage text,age_class text,discovery_entries bigint,market_count bigint,entered_lists bigint,list_reach_tier int,movement_list text,rank bigint,window_days int,day date,source_keys text);
    CREATE TABLE explore_intermediate.int_song_key__daily(platform text,platform_track_id text,song_key text);
    CREATE TABLE explore_intermediate.int_cluster_entries__daily(song_key text,day date,platform text,list_id text,event_type text,list_kind text,observed_at timestamptz,locator text,snapshot_id text);
    CREATE TABLE explore_intermediate.int_playlist__snapshots(platform text,playlist_id text,snapshot_id text,variant text,stream text,cadence text);`);
    const inputs = [
      {
        song: "add",
        platform: "spotify",
        list: "editors",
        kind: "editorial",
        event: "add",
        start: "2020-01-01T00:00:00Z",
      },
      {
        song: "head",
        platform: "apple",
        list: "new",
        kind: "new_music",
        event: "entered_head",
        start: `${day}T00:00:00Z`,
      },
      {
        song: "straddle",
        platform: "spotify",
        list: "weekly",
        kind: "editorial",
        event: "add",
        start: "2020-01-01T00:00:00Z",
      },
      {
        song: "chart",
        platform: "shazam",
        list: "shazam:top-200:us",
        kind: "chart",
        event: "chart_entry",
        start: null,
      },
      {
        song: "discovery",
        platform: "shazam",
        list: "shazam:discovery:us",
        kind: "chart",
        event: "chart_entry",
        start: null,
      },
    ];
    for (const item of inputs) {
      await db`INSERT INTO marts.mart_arrivals_current VALUES (${item.song},${item.song},${JSON.stringify([item.song])},'emerging','new',1,1,1,2,'new_entries',1,7,${day},'[]')`;
      await db`INSERT INTO explore_intermediate.int_song_key__daily VALUES ('apple',${item.song},${item.song})`;
      await db`INSERT INTO explore_intermediate.int_cluster_entries__daily VALUES (${item.song},${day},${item.platform},${item.list},${item.event},${item.kind},${`${day}T12:00:00Z`},${JSON.stringify({ window: { start: item.start }, row_key: { variant: "full", stream: "full" } })},${item.song})`;
      await db`INSERT INTO explore_intermediate.int_playlist__snapshots VALUES (${item.platform},${item.list},${item.song},'full','full',${item.song === "add" ? "daily" : "weekly"})`;
    }
    const rows = await db.unsafe(traySql, [day]);
    expect(rows.map((r) => r.song_key).sort()).toEqual([
      "add",
      "discovery",
      "head",
    ]);
    // A missed Saturday read must never freeze Friday's or Sunday's build, even an empty tray.
    const database = db;
    const control = vi.spyOn(clients, "controlStore").mockReturnValue(database);
    const warehouse = vi.spyOn(clients, "warehouse").mockReturnValue(database);
    budget.setRunner("idle");
    const cycle = randomUUID();
    const stamp = vi
      .spyOn(callReads, "stamp")
      .mockImplementation(async (_tx, relation) => ({
        relation,
        scope: "global",
        tenant_slug: null,
        stamped: true,
        cycle_id: cycle,
        close_no: "68",
        built_at: `${day}T03:00:00Z`,
      }));
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(`${day}T12:00:00Z`));
    try {
      await db`INSERT INTO control.cycle(id,cadence,scope,opened_at,opened_by_dbt_run_id,status,close_no)
        VALUES (${cycle},'daily','global',${`${week}T02:54:00Z`},'core:daily:global:fixture','closed',68)`;
      expect(await ensureDraft(week)).toBeNull();
      const sunday = new Date(`${day}T02:54:00Z`);
      sunday.setUTCDate(sunday.getUTCDate() + 1);
      await db`UPDATE control.cycle SET opened_at=${sunday.toISOString()} WHERE id=${cycle}`;
      expect(await ensureDraft(week)).toBeNull();
      expect(await readDraft(db, week)).toBeNull();
      await db`UPDATE control.cycle SET opened_at=${`${day}T02:54:00Z`} WHERE id=${cycle}`;
      const frozen = await ensureDraft(week);
      expect(frozen?.week_start).toBe(week);
      expect(frozen?.candidates.map((c) => c.song_key).sort()).toEqual([
        "add",
        "discovery",
        "head",
      ]);
      expect(
        frozen?.candidates.every(
          (c) => c.snapshot.facts_day === day && c.snapshot.draft_week === week,
        ),
      ).toBe(true);
      expect(await ensureDraft(week)).toEqual(frozen);
    } finally {
      vi.useRealTimers();
      stamp.mockRestore();
      control.mockRestore();
      warehouse.mockRestore();
    }
    await db`DROP SCHEMA marts CASCADE`;
    await db`DROP SCHEMA explore_intermediate CASCADE`;
  },
);

it.skipIf(!url)(
  "the control runtime can freeze, back and close without broader grants",
  async () => {
    if (!db || !url) return;
    const local = new URL(url);
    local.pathname = `/${name}`;
    local.username = "control_rt";
    local.password = "control_rt";
    const runtime = postgres(local.toString());
    try {
      await freezeDraft(runtime, week, [candidate()]);
      await backRule(runtime, current, week, "new-discovery");
      expect(
        (await closeDraft(runtime, week, "role", "scheduler")).closed_at,
      ).not.toBeNull();
      await expect(
        runtime`UPDATE control.showcase_rule SET conditions='[]'`,
      ).rejects.toThrow(/permission denied/);
      await expect(
        runtime`UPDATE control.showcase_call SET draft_week=NULL`,
      ).rejects.toThrow(/permission denied/);
    } finally {
      await runtime.end();
    }
  },
);

it("keeps every rule condition in SQL and every SQL condition in the vocabulary", () => {
  const query = inventory.find((q) => "id" in q && q.id === "rule_picks");
  if (!query || typeof query.sql !== "string")
    throw new Error("Open ops/showcase/queries.json and add rule_picks.");
  const conditions = query.sql
    .split("CASE condition->>'field'")[1]!
    .split("ELSE false")[0]!;
  const fields = [...conditions.matchAll(/WHEN '([^']+)'/g)].map(
    (match) => match[1],
  );
  expect(fields.sort()).toEqual(
    ruleCondition.options.map((option) => option.shape.field.value).sort(),
  );
});
it.skipIf(!url)(
  "names unopened drafts and unavailable rules separately",
  async () => {
    if (!db) return;
    await expect(
      closeDraft(db, week, "missing", "scheduler"),
    ).rejects.toMatchObject({ code: "draft_not_open" });
    await expect(backRule(db, current, week, "missing")).rejects.toMatchObject({
      code: "draft_not_open",
    });
    const song = candidate();
    await expect(
      submitCall(db, current, signCall(song.snapshot)),
    ).rejects.toMatchObject({ code: "draft_not_open" });
    await freezeDraft(db, week, [song]);
    await expect(backRule(db, current, week, "missing")).rejects.toMatchObject({
      code: "rule_not_found",
    });
  },
);
it.skipIf(!url)(
  "orders Rising before other lists, then rank and the week hash",
  async () => {
    if (!db) return;
    const songs = [
      "unplaced",
      "catalog_entries",
      "established_entries",
      "new_entries",
      "new_entries",
    ].map((list, index) => ({
      ...candidate(`order-${index}`),
      movement_list: list,
      rank: list === "new_entries" ? 99 : 1,
    }));
    await freezeDraft(db, week, songs);
    await backRule(db, current, week, "new-lists");
    await db`UPDATE control.showcase_rule SET limit_per_draft=1 WHERE id='new-lists'`;
    await closeDraft(db, week, "order", "scheduler");
    const rising = songs
      .filter((song) => song.movement_list === "new_entries")
      .sort((a, b) => {
        const hash = (song: string) =>
          createHash("md5").update(`${week}:${song}`).digest("hex");
        return hash(a.song_key).localeCompare(hash(b.song_key));
      });
    expect(await db`SELECT song_key FROM control.showcase_call`).toEqual([
      { song_key: rising[0]!.song_key },
    ]);
  },
);
it.skipIf(!url)(
  "keeps a closer's display name after key rotation",
  async () => {
    if (!db) return;
    await db`INSERT INTO control.showcase_actor(api_key_id,handle,display_name,retired_at)
    VALUES (${current.person.api_key_id},${current.handle},'Test viewer',now()) ON CONFLICT DO NOTHING`;
    expect(await draftCloser(db, `api-key:${current.person.api_key_id}`)).toBe(
      "Test viewer",
    );
    expect(await draftCloser(db, "scheduler")).toBeNull();
  },
);

it.skipIf(!url)(
  "first boot after the deadline leaves no draft, rules or close to publish",
  async () => {
    if (!db) return;
    const missed = "2026-09-25";
    const afterClose = new Date("2026-09-26T22:00:00Z");
    expect(await freezeDraftAt(db, missed, [], () => afterClose)).toBeNull();
    expect(
      await freezeDraftAt(db, missed, [candidate()], () => afterClose),
    ).toBeNull();
    await closeDueDrafts(db);
    expect(await readDraft(db, missed)).toBeNull();
    expect(await db`SELECT id FROM control.showcase_rule`).toHaveLength(0);
    expect(await db`SELECT id FROM control.audit_log`).toHaveLength(0);
    const existing = await freezeDraft(db, missed, [candidate()]);
    expect(await freezeDraftAt(db, missed, [], () => afterClose)).toEqual(
      existing,
    );
  },
);
