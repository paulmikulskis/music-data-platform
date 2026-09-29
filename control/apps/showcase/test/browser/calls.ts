import { randomUUID } from "node:crypto";
import { callSnapshot, callWeek, earlierWeek } from "../../lib/calls";
import { signCall } from "../../server/call-token";
import { z } from "zod";
import { chartPlaces, keys } from "./fixtures";
import assert from "node:assert/strict";
import path from "node:path";
import type { Page } from "@playwright/test";
import type { Sql } from "postgres";
import { density, overlay, densityPolicy } from "./density.mjs";
import { withServerTime } from "./clock";
import { writeFile } from "node:fs/promises";
async function callsWalkSteps(
  page: Page,
  db: Sql,
  wh: Sql,
  origin: string,
  out: string,
  size: string,
  song: string,
) {
  const measurements: Record<string, Awaited<ReturnType<typeof density>>> = {};
  const sheets: Record<string, Awaited<ReturnType<typeof overlay>>> = {};
  // A screen is measured on the whole phone viewport. A sheet is measured on its own,
  // like every other viewer sheet: at most 80 words and no banned words.
  const capture = async (name: string, sheet = false) => {
    await page.waitForTimeout(350);
    await page.screenshot({ path: path.join(out, `${name}-${size}.png`) });
    if (sheet) {
      const measured = await page.evaluate(overlay, {
        kind: "sheet",
        policy: densityPolicy,
      });
      sheets[name] = measured;
      assert(measured.found, `${name}: the sheet is open`);
      assert(
        measured.words <= 80,
        `${name}: ${measured.words} words in a sheet: ${measured.text}`,
      );
      assert(!measured.banned.length, `${name}: ${measured.banned}`);
      return;
    }
    const measured = await page.evaluate(density, densityPolicy);
    measurements[name] = measured;
    if (size.startsWith("390")) {
      assert(
        measured.words <= 40,
        `${name}: ${measured.words} words: ${measured.text}`,
      );
      assert(
        measured.numbers <= 3,
        `${name}: ${measured.numbers} numbers: ${measured.text}`,
      );
      assert(!measured.banned.length, `${name}: ${measured.banned}`);
      assert(!measured.headlinePatterns.length, `${name}: headline copy`);
      assert(
        measured.headlines.length === 1 &&
          measured.headlines.every((n) => n <= 8),
        `${name}: headline budget`,
      );
      assert(
        measured.cards.every((n) => n === 1),
        `${name}: primary actions`,
      );
    }
  };
  await db`DELETE FROM control.showcase_call WHERE author='fixture'`;
  await page.goto(origin + "/today");
  const callButton = page.getByRole("button", { name: "Call it", exact: true });
  await callButton.waitFor();
  // Refusals run once Home has settled, so they do not race its own session reads.
  for (const route of ["/calls", "/calls/view"]) {
    // The API request context does not send the secure session cookie over http, so pass it.
    const cookie = (await page.context().cookies())
      .map((c) => `${c.name}=${c.value}`)
      .join("; ");
    const refused = await page.request.post(origin + route, {
      headers: { origin: "https://foreign.invalid", cookie },
      data: {},
    });
    assert.equal(refused.status(), 403);
    const signedOut = await fetch(origin + route, {
      method: "POST",
      headers: { origin, "Content-Type": "application/json" },
      body: "{}",
    });
    assert.equal(signedOut.status, 401);
  }
  // Keyboard and tap share the confirm path. An early release never saves.
  await callButton.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("dialog").waitFor();
  await capture("call-confirm", true);
  await page.getByRole("button", { name: "Not now", exact: true }).click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  const box = await callButton.boundingBox();
  assert(box);
  await page.mouse.move(box.x + 10, box.y + 10);
  await page.mouse.down();
  await page.waitForTimeout(180);
  assert.equal(
    await callButton.evaluate((button) => {
      const ring = button.querySelector("circle");
      return ring ? getComputedStyle(ring).animationName : null;
    }),
    "none",
  );
  await page.mouse.up();
  assert.equal(
    (
      await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author='fixture'`
    )[0].n,
    0,
  );
  await page.getByRole("button", { name: "Not now", exact: true }).click();
  await page.mouse.move(box.x + 10, box.y + 10);
  await page.mouse.down();
  await page.waitForTimeout(500);
  await page.mouse.up();
  await page.getByText(/^called ·/).waitFor();
  await capture("home-stamped");
  assert.equal(
    await page
      .locator("[data-card]:has(.call-stamp)")
      .evaluate((card) => getComputedStyle(card).animationName),
    "none",
  );
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const [call] =
    await db`SELECT id,facts,snapshot FROM control.showcase_call WHERE author='fixture' ORDER BY submitted_at DESC LIMIT 1`;
  assert(call);
  assert.deepEqual(JSON.parse(call.snapshot), call.facts);
  assert.equal(call.facts.card, "mover");
  assert.equal(call.facts.places_shown, null);
  // The mover's facts freeze as rendered, each with its own window (2 days) beside the card's (3 days).
  await page
    .getByRole("button", { name: /^See why/ })
    .first()
    .click();
  const factButtons = page.locator(".music-facts").first().locator("button");
  await factButtons.first().waitFor();
  const renderedFacts = await factButtons.allInnerTexts();
  const frozenFacts = z
    .array(
      z.object({
        component: z.string(),
        text: z.string(),
        window_days: z.number().nullable(),
      }),
    )
    .parse(call.facts.shown);
  assert.deepEqual(
    frozenFacts.map((fact) => fact.text),
    renderedFacts,
  );
  assert.equal(call.facts.window_days, 3);
  const streams = frozenFacts.find((f) => f.component === "stream_rate_gain");
  assert.equal(streams?.window_days, 2);
  assert.match(streams?.text ?? "", /over 2 days/);
  await page.getByRole("button", { name: "Turn cover back" }).first().click();
  // The next card's confirm sheet counts the call just made on this one.
  await page.getByRole("button", { name: "Next song" }).click();
  // Let the stack finish moving so the sheet belongs to the settled card.
  await page.waitForTimeout(800);
  await page
    .getByRole("button", { name: "Call it", exact: true })
    .first()
    .click();
  const confirm = await page.getByRole("dialog").innerText();
  assert.match(confirm, /\b4\b[\s\S]{0,40}calls left this week/);
  await page.getByRole("button", { name: "Not now", exact: true }).click();
  await db`UPDATE control.showcase_call SET submitted_at=now()-interval '7 days' WHERE id=${call.id}`;
  await page.goto(origin + "/today");
  await page.locator(".calls-strip").waitFor();
  await capture("home-calls-strip");
  await page.locator(".calls-strip").click();
  await page.waitForURL("**/picks?new=1");
  await capture("calls-board");
  await page.getByRole("button", { name: "Shazam", exact: true }).click();
  await capture("calls-history", true);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page
    .getByRole("link", { name: /Open call:/ })
    .first()
    .focus();
  await page.keyboard.press("Enter");
  await page.waitForURL(`**/picks/${call.id}`);
  const [view] =
    await db`SELECT subject FROM control.audit_log WHERE action='showcase.calls_view' ORDER BY at DESC LIMIT 1`;
  assert.match(view.subject, /^\d{4}-\d{2}-\d{2}$/);
  await page.goto(`${origin}/picks/${call.id}`);
  await capture("call-front");
  // The keyboard flips the card and keeps focus on the flip control.
  await page.getByRole("button", { name: "Flip call card" }).focus();
  await page.keyboard.press("Enter");
  await page
    .getByText("seen since, not on the card", { exact: true })
    .waitFor();
  assert.equal(
    await page.evaluate(() =>
      document.activeElement?.getAttribute("aria-label"),
    ),
    "Flip call card",
  );
  await capture("call-back");
  await page.locator(".record-card").screenshot({
    path: path.join(out, `call-places-line-${size}.png`),
  });
  await page.getByRole("button", { name: "Share", exact: true }).click();
  await capture("call-share-refused", true);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "See places", exact: true }).click();
  await page
    .getByText("Places weren't on this card.", { exact: true })
    .waitFor();
  // The sheet shows the card's frozen facts first.
  await page
    .getByRole("dialog")
    .getByText(streams?.text ?? "", { exact: true })
    .waitFor();
  await capture("places-not-shown", true);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  // A frozen country baseline remains above the call line.
  const populated = signCall(
    callSnapshot.parse({
      ...call.facts,
      places_shown: [{ country: "germany", city: null }],
    }),
  );
  const populatedId = randomUUID();
  await db`UPDATE control.showcase_call SET id=${populatedId},snapshot=${populated.body},facts=${populated.body}::text::jsonb WHERE id=${call.id}`;
  call.id = populatedId;
  await page.goto(`${origin}/picks/${call.id}`);
  await page.getByRole("button", { name: "See places", exact: true }).click();
  assert.equal(await page.getByText("Germany", { exact: true }).count(), 1);
  await capture("places-populated", true);
  const detail = page.locator(".place-names button").first();
  if (await detail.count()) {
    await detail.click();
    await capture("call-place-detail", true);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await page.getByRole("button", { name: "See places", exact: true }).click();
  }
  await page.getByRole("button", { name: "Open map" }).click();
  await capture("calls-map", true);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Hide", exact: true }).click();
  await capture("call-hide", true);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  // A locked local warehouse exercises the actual last-good and empty fallback.
  await wh.begin(async (tx) => {
    await tx`LOCK TABLE marts.mart_shazam_chart_daily IN ACCESS EXCLUSIVE MODE`;
    await page.goto(`${origin}/picks/${call.id}`);
    await capture("call-last-good");
    const [previous] =
      await db`INSERT INTO control.showcase_call(author,author_kind,idempotency_key,song_key,anchors,snapshot,facts,facts_day,close_no,week_start)
      SELECT author,author_kind,${randomUUID()},song_key,anchors,snapshot,facts,facts_day,close_no,week_start-7 FROM control.showcase_call WHERE id=${call.id} RETURNING id,week_start::text`;
    await page.goto(`${origin}/picks?week=${previous.week_start}`);
    await capture("calls-unavailable");
    await page.goto(`${origin}/picks/${previous.id}`);
    await capture("call-unavailable");
    await db`DELETE FROM control.showcase_call WHERE id=${previous.id}`;
  });
  const base = callSnapshot.parse(call.facts);
  for (const variant of ["unmatched", "changed", "old"]) {
    const id = randomUUID();
    const facts = {
      ...base,
      idempotency_key: randomUUID(),
      anchors: base.anchors.map((a) =>
        variant === "old"
          ? a
          : {
              ...a,
              song_key: "earlier-identity",
              ...(variant === "unmatched"
                ? {
                    platform: "spotify",
                    platform_track_id: "unmatched-fixture",
                  }
                : {}),
            },
      ),
    };
    const signed = signCall(facts);
    await db`INSERT INTO control.showcase_call(id,author,author_kind,idempotency_key,song_key,anchors,snapshot,facts,facts_day,close_no,week_start,submitted_at)
      SELECT ${id},author,author_kind,${facts.idempotency_key},song_key,anchors,${signed.body},${signed.body}::text::jsonb,facts_day,close_no,${earlierWeek(earlierWeek(callWeek()))},now()-(${variant === "old" ? 29 : 0} * interval '1 day') FROM control.showcase_call WHERE id=${call.id}`;
    await page.goto(`${origin}/picks/${id}`);
    await capture(`call-${variant}`);
    if (variant === "unmatched") {
      await page
        .getByRole("button", {
          name: "Apple Music match pending →",
          exact: true,
        })
        .click();
      await capture("call-match-pending", true);
      await page.getByRole("button", { name: "Close", exact: true }).click();
      await page
        .getByRole("button", { name: "See places", exact: true })
        .click();
      await page.getByText("Nothing new yet.", { exact: false }).waitFor();
      await capture("places-nothing-new", true);
      await page.getByRole("button", { name: "Close", exact: true }).click();
    }
    const missingProof = page.getByRole("button", {
      name: "proof →",
      exact: true,
    });
    if (await missingProof.count()) {
      await missingProof.click();
      await capture(`call-${variant}-proof`, true);
      await page.getByRole("button", { name: "Close", exact: true }).click();
    }
    await db`DELETE FROM control.showcase_call WHERE id=${id}`;
  }
  // A Spotify-only call whose song key shares a provisional group with an Apple copy:
  // its places come from that copy and say so, and it is not "match pending".
  {
    const id = randomUUID();
    const facts = {
      ...base,
      idempotency_key: randomUUID(),
      song_key: keys[1],
      anchors: [
        {
          platform: "spotify",
          platform_track_id: `lead-${keys[1]}`,
          song_key: keys[1],
        },
      ],
    };
    const signed = signCall(facts);
    await db`INSERT INTO control.showcase_call(id,author,author_kind,idempotency_key,song_key,anchors,snapshot,facts,facts_day,close_no,week_start,submitted_at)
      SELECT ${id},author,author_kind,${facts.idempotency_key},${keys[1]},anchors,${signed.body},${signed.body}::text::jsonb,facts_day,close_no,${earlierWeek(earlierWeek(callWeek()))},now()-interval '7 days' FROM control.showcase_call WHERE id=${call.id}`;
    await page.goto(`${origin}/picks/${id}`);
    await page
      .getByText("Places include matched copies.", { exact: false })
      .waitFor();
    assert.equal(
      await page
        .getByText("Apple Music match pending", { exact: false })
        .count(),
      0,
    );
    await capture("call-grouped");
    await page.getByRole("button", { name: "See places", exact: true }).click();
    await page.locator(".place-names button").first().click();
    await page.getByText("matched copy", { exact: false }).waitFor();
    await capture("call-place-copy", true);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await db`DELETE FROM control.showcase_call WHERE id=${id}`;
  }
  await db`DELETE FROM control.showcase_call WHERE author='fixture'`;
  await page.goto(`${origin}/s/song/${song}`);
  await page.getByRole("button", { name: "Places →", exact: true }).click();
  await capture("song-places", true);
  // City slugs arrive URL-encoded; the sheet shows their names.
  const placesSheet = await page.getByRole("dialog").innerText();
  assert.match(placesSheet, /São Paulo/);
  assert.doesNotMatch(placesSheet, /%[0-9A-F]{2}/);
  const rendered = await page
    .locator("[data-call-place]")
    .evaluateAll((nodes) =>
      nodes.map((n) => JSON.parse(n.getAttribute("data-call-place") ?? "null")),
    );
  assert.deepEqual(
    rendered,
    chartPlaces.map(({ country, city }) => ({ country, city })),
  );
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await freezeVisibleCard(page, db, "song", rendered);
  // Refusal surfaces use actual server replies for this authenticated mutation route.
  for (const code of ["call_limit", "call_snapshot_expired", "call_invalid"]) {
    await db`DELETE FROM control.showcase_call WHERE author='fixture'`;
    await page.goto(`${origin}/s/song/${song}`);
    await page.route("**/calls", (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ error: code }),
      }),
    );
    await page.getByRole("button", { name: "Call it", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Call it", exact: true })
      .click();
    await page.waitForTimeout(250);
    await capture(code.replaceAll("_", "-"), true);
    await page.unroute("**/calls");
    if (code === "call_snapshot_expired") {
      await page
        .getByRole("heading", { name: "Call the refreshed card?", exact: true })
        .waitFor();
      assert.equal(
        (
          await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author='fixture'`
        )[0].n,
        0,
      );
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Call it", exact: true })
        .click();
      await page.getByText(/^called ·/).waitFor();
    }
  }
  await db`DELETE FROM control.showcase_call WHERE author='fixture'`;
  await page.goto(`${origin}/s/song/${song}`);
  const attempts: string[] = [];
  await page.route("**/calls", async (route) => {
    attempts.push(
      z
        .object({ signed: z.object({ body: z.string() }) })
        .parse(route.request().postDataJSON()).signed.body,
    );
    if (attempts.length === 1) {
      await route.fetch();
      await route.abort("connectionreset");
    } else await route.continue();
  });
  await page.getByRole("button", { name: "Call it", exact: true }).focus();
  await page.keyboard.press("Space");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Call it", exact: true })
    .click();
  await page.getByRole("status").filter({ hasText: "checking" }).waitFor();
  await capture("call-checking");
  await page.getByText(/^called ·/).waitFor();
  await capture("song-stamped");
  assert.equal(attempts.length, 2);
  assert.equal(attempts[0], attempts[1]);
  await page.unroute("**/calls");
  await page.getByRole("button", { name: "Undo", exact: true }).focus();
  await page.keyboard.press("Enter");
  await capture("call-undo", true);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Undo", exact: true })
    .click();
  await page.getByRole("button", { name: "Call it", exact: true }).waitFor();
  assert(
    (
      await db`SELECT undone_at FROM control.showcase_call WHERE author='fixture'`
    )[0].undone_at,
  );
  // The other viewer's call: the song offers Call it too, and the board shows the other ear.
  await db`UPDATE control.showcase_call SET author='other-fixture', undone_at=NULL WHERE author='fixture'`;
  await page.goto(`${origin}/s/song/${song}`);
  await page
    .getByRole("button", { name: "Call it too", exact: true })
    .waitFor();
  await capture("call-it-too");
  await page.goto(origin + "/picks");
  await page.getByRole("heading", { name: "other ear", exact: true }).waitFor();
  assert.equal(
    await page.getByRole("heading", { name: "yours", exact: true }).count(),
    0,
  );
  await capture("calls-board-other");
  await db`DELETE FROM control.showcase_call WHERE author IN ('fixture','other-fixture')`;
  await page.goto(origin + "/picks");
  await capture("calls-empty");
  // An earlier empty week names its Friday and leads back to this week.
  await page
    .getByRole("link", { name: "Earlier weeks →", exact: true })
    .click();
  await page.waitForURL(`**/picks?week=${earlierWeek(callWeek())}`);
  await page.getByText("No calls that week.", { exact: true }).waitFor();
  await capture("calls-earlier-empty");
  await page.getByRole("link", { name: "Open this week", exact: true }).click();
  await page.waitForURL("**/picks");
  await writeFile(
    path.join(out, `calls-density-${size}.json`),
    JSON.stringify({ screens: measurements, sheets }, null, 2) + "\n",
  );
}
export async function freezeVisibleCard(
  page: Page,
  db: Sql,
  kind: string,
  places: { country: string | null; city: string | null }[] | null,
) {
  await db`DELETE FROM control.showcase_call WHERE author='fixture'`;
  await page
    .getByRole("button", { name: "Call it", exact: true })
    .first()
    .waitFor();
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/calls") && r.request().method() === "POST",
  );
  void response.catch(() => {});
  await page
    .getByRole("button", { name: "Call it", exact: true })
    .first()
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Call it", exact: true })
    .click();
  assert.equal((await response).status(), 200);
  const [call] =
    await db`SELECT facts,snapshot FROM control.showcase_call WHERE author='fixture' ORDER BY submitted_at DESC LIMIT 1`;
  assert.equal(call.facts.card, kind);
  assert.deepEqual(call.facts.places_shown, places);
  assert.deepEqual(JSON.parse(call.snapshot), call.facts);
  await db`DELETE FROM control.showcase_call WHERE author='fixture'`;
}

// Picks are stamped by the server's real clock and Undo is judged against that stamp,
// so the whole walk runs at real time.
export async function callsWalk(...args: Parameters<typeof callsWalkSteps>) {
  return withServerTime(args[0], () => callsWalkSteps(...args));
}
