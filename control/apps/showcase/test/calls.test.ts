import { afterAll, beforeEach, expect, it, vi } from "vitest";
import { randomUUID } from "node:crypto";
import postgres from "postgres";
import type { Session } from "@mdp/showcase-auth";
import type { Build } from "../components/number";
vi.mock("server-only", () => ({}));
import { callWeek, type CallSnapshot } from "../lib/calls";
import { signCall, verifyCall } from "../server/call-token";
import { submitCall, changeCall } from "../server/call-store";
const url = process.env.MDP_CALL_TEST_URL ?? "";
const db = url ? postgres(url, { max: 6 }) : null;
const session: Session = {
  id_hash: "fixture",
  handle: `fixture-${randomUUID()}`,
  csrf_token: "fixture",
  person: {
    handle: "fixture",
    display_name: "Test viewer",
    email: "fixture@example.invalid",
    api_key_id: randomUUID(),
    admin_key: "fixture",
  },
};
const build = {
  relation: "marts.mart_arrivals_current",
  scope: "global" as const,
  tenant_slug: null,
  stamped: true,
  cycle_id: randomUUID(),
  close_no: "68",
  built_at: "2026-09-26T03:00:00Z",
};
function snapshot(song = randomUUID()): CallSnapshot {
  return {
    v: 1,
    card: "arrival",
    song_key: song,
    anchors: [{ platform: "apple", platform_track_id: song, song_key: song }],
    anchors_truncated: false,
    places_shown: [{ country: "DE", city: null }],
    builds: [build],
    source_keys: ["sz_chart"],
    facts_day: "2026-09-26",
    close_no: "68",
    idempotency_key: randomUUID(),
    handle: session.handle,
    exp: Date.now() + 600000,
    list: "new_entries",
    window_days: 3,
    entered_lists: "2",
    discovery: ["DE"],
  };
}
beforeEach(() => {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
});
afterAll(async () => {
  vi.useRealTimers();
  await db?.end();
});
it("uses the Friday boundary in New York through daylight saving changes", () => {
  expect(callWeek(new Date("2026-09-25T03:59:59Z"))).toBe("2026-09-18");
  expect(callWeek(new Date("2026-09-25T04:00:00Z"))).toBe("2026-09-25");
  expect(callWeek(new Date("2026-12-04T04:59:59Z"))).toBe("2026-11-27");
  expect(callWeek(new Date("2026-12-04T05:00:00Z"))).toBe("2026-12-04");
});
it("binds signed text to its handle, key and expiry", () => {
  const facts = snapshot();
  const signed = signCall(facts);
  expect(JSON.parse(signed.body)).toEqual(facts);
  expect(verifyCall(signed, session.handle)).toEqual(facts);
  expect(verifyCall(signed, "another")).toBeNull();
  expect(
    verifyCall(
      { ...signed, body: signed.body.replace('"68"', '"69"') },
      session.handle,
    ),
  ).toBeNull();
  expect(verifyCall(signed, session.handle, facts.exp)).toBeNull();
});
const buildCases: {
  name: string;
  change: Partial<Build>;
  accepted: boolean;
}[] = [
  { name: "matching builds", change: {}, accepted: true },
  {
    name: "another cycle",
    change: { cycle_id: randomUUID() },
    accepted: false,
  },
  { name: "another close", change: { close_no: "69" }, accepted: false },
  {
    name: "no stamp",
    change: { stamped: false, built_at: null },
    accepted: false,
  },
  { name: "no cycle", change: { cycle_id: null }, accepted: false },
  { name: "no close", change: { close_no: null }, accepted: false },
];
it.skipIf(!db).each(buildCases)(
  "submission checks $name even with a valid signature",
  async ({ change, accepted }) => {
    if (!db) return;
    const who = { ...session, handle: `fixture-${randomUUID()}` };
    const facts = { ...snapshot(), handle: who.handle };
    facts.builds.push({
      ...build,
      relation: "explore_intermediate.int_song_key__daily",
      ...change,
    });
    // A valid signature can come from an older server. Submission still checks its facts.
    const signed = signCall(facts);
    if (accepted) {
      expect((await submitCall(db, who, signed)).facts).toEqual(facts);
    } else {
      await expect(submitCall(db, who, signed)).rejects.toMatchObject({
        code: "call_snapshot_expired",
      });
    }
    const [saved] =
      await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author=${who.handle}`;
    const [audit] =
      await db`SELECT count(*)::int AS n FROM control.audit_log WHERE actor=${`api-key:${who.person.api_key_id}`} AND subject IN (SELECT 'call:' || id FROM control.showcase_call WHERE author=${who.handle})`;
    expect(saved.n).toBe(accepted ? 1 : 0);
    expect(audit.n).toBe(accepted ? 1 : 0);
  },
);
it("rejects a signed snapshot whose top-level close disagrees with its builds", () => {
  expect(
    verifyCall(signCall({ ...snapshot(), close_no: "69" }), session.handle),
  ).toBeNull();
});
it.skipIf(!db)(
  "stores signed bytes and one audit for concurrent identical requests; retries survive expiry and restart",
  async () => {
    if (!db) return;
    const signed = signCall(snapshot());
    const [one, two] = await Promise.all([
      submitCall(db, session, signed),
      submitCall(db, session, signed),
    ]);
    expect(one).toEqual(two);
    // Times leave the server as ISO 8601 UTC, which every browser parses.
    expect(one.submitted_at).toMatch(
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/,
    );
    expect(Date.parse(one.submitted_at)).not.toBeNaN();
    const [row] =
      await db`SELECT snapshot,facts FROM control.showcase_call WHERE id=${one.id}`;
    expect(row.snapshot).toBe(signed.body);
    expect(row.facts).toEqual(JSON.parse(signed.body));
    const [audit] =
      await db`SELECT count(*)::int AS n FROM control.audit_log WHERE action='showcase.call' AND subject=${`call:${one.id}`}`;
    expect(audit.n).toBe(1);
    const restarted = postgres(url);
    try {
      vi.spyOn(Date, "now").mockReturnValue(Date.now() + 86400000);
      expect(await submitCall(restarted, session, signed)).toEqual(one);
    } finally {
      vi.restoreAllMocks();
      await restarted.end();
    }
  },
);
it.skipIf(!db)(
  "returns the same call for concurrent retries across Friday rollover",
  async () => {
    if (!db) return;
    const who = { ...session, handle: `fixture-${randomUUID()}` };
    const signed = signCall({ ...snapshot(), handle: who.handle });
    // Project the database clock onto either side of Friday. The second request
    // must return under the key lock without asking for a new week.
    let clockReads = 0;
    const clocked = postgres(url, {
      max: 2,
      transform: {
        value: {
          from(value, column) {
            if (column.name !== "at") return value;
            return clockReads++ === 0
              ? "2026-09-25T03:59:59Z"
              : "2026-09-25T04:00:01Z";
          },
        },
      },
    });
    try {
      const [one, two] = await Promise.all([
        submitCall(clocked, who, signed),
        submitCall(clocked, who, signed),
      ]);
      expect(two).toEqual(one);
      expect(one.week_start).toBe("2026-09-18");
      const [rows] =
        await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author=${who.handle}`;
      expect(rows.n).toBe(1);
      expect(clockReads).toBe(1);
      expect(await submitCall(clocked, who, signed)).toEqual(one);
      const [audit] =
        await db`SELECT count(*)::int AS n FROM control.audit_log WHERE action='showcase.call' AND subject=${`call:${one.id}`}`;
      expect(audit.n).toBe(1);
    } finally {
      await clocked.end();
    }
  },
);
it.skipIf(!db)(
  "admits only one of two concurrent new calls after four accepted calls",
  async () => {
    if (!db) return;
    const who = { ...session, handle: `fixture-${randomUUID()}` };
    const card = () => signCall({ ...snapshot(), handle: who.handle });
    for (let i = 0; i < 4; i++) await submitCall(db, who, card());
    const answers = await Promise.allSettled([
      submitCall(db, who, card()),
      submitCall(db, who, card()),
    ]);
    expect(answers.filter((a) => a.status === "fulfilled")).toHaveLength(1);
    const refused = answers.find((a) => a.status === "rejected");
    expect(refused?.status === "rejected" && refused.reason.code).toBe(
      "call_limit",
    );
    const [count] =
      await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author=${who.handle}`;
    expect(count.n).toBe(5);
  },
);
it.skipIf(!db)(
  "checks shared anchors; undo uses a slot; late undo is refused and hide preserves the record",
  async () => {
    if (!db) return;
    const who = { ...session, handle: `fixture-${randomUUID()}` };
    const first = { ...snapshot(), handle: who.handle };
    const call = await submitCall(db, who, signCall(first));
    expect(
      (
        await submitCall(
          db,
          who,
          signCall({
            ...first,
            idempotency_key: randomUUID(),
            song_key: "different-display",
          }),
        )
      ).id,
    ).toBe(call.id);
    expect(
      (await changeCall(db, who, call.id, "undo")).undone_at,
    ).not.toBeNull();
    const next = await submitCall(
      db,
      who,
      signCall({ ...first, idempotency_key: randomUUID() }),
    );
    expect(next.id).not.toBe(call.id);
    await db`UPDATE control.showcase_call SET submitted_at=now()-interval '11 minutes' WHERE id=${next.id}`;
    await expect(changeCall(db, who, next.id, "undo")).rejects.toMatchObject({
      code: "call_undo_expired",
    });
    expect(
      (await changeCall(db, who, next.id, "hide")).hidden_at,
    ).not.toBeNull();
    const [count] =
      await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author=${who.handle}`;
    expect(count.n).toBe(2);
    await expect(
      changeCall(db, session, next.id, "hide"),
    ).rejects.toMatchObject({ code: "call_not_found" });
  },
);
it("freezes each mover fact with its own window beside the card window", async () => {
  const { moverProjection } = await import("../server/call-offers");
  const { mover } = await import("../server/models");
  const { musicFacts } = await import("../lib/music-facts");
  const { movers } = await import("./browser/fixtures");
  // The fixture's stream fact has a two-day window on a three-day card.
  const row = mover.parse(movers[0]);
  const card = moverProjection(row, build);
  expect(card.window_days).toBe(3);
  expect(card.shown.map((fact) => fact.text)).toEqual(
    musicFacts(row).map((fact) => fact.text),
  );
  const streams = card.shown.find(
    (fact) => fact.component === "stream_rate_gain",
  );
  expect(streams?.window_days).toBe(2);
  expect(streams?.text).toMatch(/over 2 days/);
  expect(
    card.parts.find((part) => part.component === "stream_rate_gain")
      ?.window_days,
  ).toBe(2);
});
