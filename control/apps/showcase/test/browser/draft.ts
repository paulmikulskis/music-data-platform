import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import type { Sql } from "postgres";
import type { Page } from "@playwright/test";
import { callWeek, earlierWeek } from "../../lib/calls";
import { draftReadDay, type DraftCandidate } from "../../lib/draft";
import { freezeDraft } from "../../server/draft-store";
import { build, keys, movers } from "./fixtures";
import { density, overlay, densityPolicy } from "./density.mjs";
export async function draftWalk(
  page: Page,
  db: Sql,
  wh: Sql,
  origin: string,
  out: string,
  size: string,
) {
  const measurements: Record<string, unknown> = {};
  const capture = async (name: string, sheet = false) => {
    await page.waitForTimeout(400);
    if (!sheet) {
      const visibleCovers = await page
        .locator(".draft-picks .art")
        .evaluateAll((covers) =>
          covers.every(
            (cover) => Number(getComputedStyle(cover).opacity) > 0.9,
          ),
        );
      assert(visibleCovers, `${name}: every saved cover stays visible`);
    }
    await page.screenshot({ path: path.join(out, `${name}-${size}.png`) });
    if (sheet) {
      const d = await page.evaluate(overlay, {
        kind: "sheet",
        policy: densityPolicy,
      });
      measurements[name] = d;
      assert(
        d.found && d.words <= 80 && !d.banned.length,
        `${name}: ${d.text}`,
      );
    } else {
      const d = await page.evaluate(density, densityPolicy);
      measurements[name] = d;
      if (size.startsWith("390")) {
        assert(
          d.words <= 40 && d.numbers <= 3 && !d.banned.length,
          `${name}: ${d.text}`,
        );
        assert(
          d.headlines.length === 1 &&
            d.headlines[0] <= 8 &&
            !d.headlinePatterns.length,
          `${name}: headlines`,
        );
      }
    }
  };
  await db`TRUNCATE control.showcase_call_rule,control.showcase_rule,control.showcase_call,control.showcase_draft CASCADE`;
  const week = callWeek();
  const songs: DraftCandidate[] = keys.map(
    (song, i): DraftCandidate => ({
      song_key: song,
      artist_stage: "emerging",
      age_class: "new",
      discovery_entries: 3,
      market_count: 3,
      entered_lists: 2,
      list_reach_tier: 2,
      movement_list: "new_entries",
      rank: i + 1,
      snapshot: {
        v: 1,
        card: "arrival",
        song_key: song,
        anchors: [
          {
            platform: "apple",
            platform_track_id: `draft-${i}`,
            song_key: song,
          },
        ],
        anchors_truncated: false,
        places_shown: null,
        builds: [build("marts.mart_arrivals_current")],
        source_keys: ["sz_chart"],
        facts_day: draftReadDay(week),
        close_no: "41",
        idempotency_key: randomUUID(),
        handle: "fixture",
        exp: 0,
        draft_week: week,
        list: "new_entries",
        window_days: 7,
        entered_lists: "2",
        discovery: [],
      },
    }),
  );
  await freezeDraft(db, week, songs, () => new Date(`${week}T12:00:00Z`));
  await db`UPDATE control.showcase_draft SET closes_at=now()+interval '1 day' WHERE week_start=${week}`;
  await page.goto(origin + "/draft?board=1");
  await capture("draft-open");
  for (const song of movers) {
    assert.equal(
      await page
        .getByRole("button", {
          name: `Choose ${song.title_text} by ${song.artist_text}`,
          exact: true,
        })
        .count(),
      1,
    );
  }
  // Names follow the live read even after the tray freezes.
  await wh`UPDATE marts.mart_song_day SET title_text='Evening tide' WHERE song_key=${keys[0]}`;
  await page.reload();
  await page
    .getByRole("button", {
      name: "Choose Evening tide by Test recording",
      exact: true,
    })
    .click();
  await page
    .getByRole("heading", {
      name: "Call Evening tide by Test recording?",
      exact: true,
    })
    .waitFor();
  await page.getByRole("button", { name: "Not now", exact: true }).click();
  const [frozen] =
    await db`SELECT candidates::text AS candidates FROM control.showcase_draft WHERE week_start=${week}`;
  assert(!frozen.candidates.includes("Evening tide"));
  assert(!frozen.candidates.includes("Test recording"));
  await wh`UPDATE marts.mart_song_day SET title_text='Night tide' WHERE song_key=${keys[0]}`;
  await page.reload();
  await page.getByRole("button", { name: "See rules", exact: false }).click();
  await capture("draft-rule-chips", true);
  await page
    .getByRole("button", { name: "Back rule", exact: true })
    .first()
    .click();
  await page
    .locator(".draft-columns .draft-rule-preview")
    .filter({ hasText: "new artist" })
    .waitFor();
  await page.getByRole("button", { name: "See rules", exact: false }).click();
  await page.getByRole("button", { name: "Back rule", exact: true }).click();
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".draft-columns .draft-rule-preview").length ===
      2,
  );
  const cover = page.locator(".draft-tray button").first();
  // Hold a real drag while recording its target, then drop it into ear.
  const data = await page.evaluateHandle(() => new DataTransfer());
  await cover.dispatchEvent("dragstart", { dataTransfer: data });
  await capture("draft-dragging");
  await page
    .getByRole("region", { name: "Ear column" })
    .dispatchEvent("drop", { dataTransfer: data });
  await page.locator(".draft-picks a").first().waitFor();
  const [dragged] =
    await db`SELECT song_key FROM control.showcase_call WHERE author='fixture' ORDER BY submitted_at`;
  assert.equal(dragged.song_key, keys[0], "a dragged cover becomes a call");
  await page
    .getByRole("button", {
      name: "Choose Slow return by Test recording",
      exact: true,
    })
    .click();
  await page
    .getByRole("heading", {
      name: "Call Slow return by Test recording?",
      exact: true,
    })
    .waitFor();
  await page.getByRole("button", { name: "Call it", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelectorAll(".draft-picks a").length === 2,
  );
  const [tapped] =
    await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author='fixture'`;
  assert.equal(tapped.n, 2, "tap uses the same call path");
  await page.locator(".draft-tray button").nth(2).focus();
  await page.keyboard.press("Enter");
  await capture("draft-keyboard", true);
  await page.getByRole("button", { name: "Call it", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelectorAll(".draft-picks a").length === 3,
  );
  for (const [code, name] of [
    ["call_limit", "draft-limit"],
    ["call_snapshot_expired", "draft-expired"],
    ["call_checking", "draft-checking"],
  ]) {
    await page.route("**/calls", (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ error: code, next: "/picks" }),
      }),
    );
    await page.locator(".draft-tray button").first().click();
    await page.getByRole("button", { name: "Call it", exact: true }).click();
    await capture(name, true);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await page.unroute("**/calls");
  }
  await page
    .getByRole("button", { name: "Close the draft", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Close the draft?", exact: true })
    .waitFor();
  await capture("draft-close-confirm", true);
  await page.getByRole("button", { name: "Not now", exact: true }).click();
  const [stillOpen] =
    await db`SELECT closed_at FROM control.showcase_draft WHERE week_start=${week}`;
  assert.equal(stillOpen.closed_at, null, "Not now leaves the draft open");
  await page.route("**/draft/action", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "draft_close_failed", next: "/draft" }),
    }),
  );
  await page
    .getByRole("button", { name: "Close the draft", exact: true })
    .click();
  await page
    .locator(".sheet .primary")
    .filter({ hasText: /^Close$/ })
    .click();
  await capture("draft-close-failed", true);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.unroute("**/draft/action");
  await page
    .getByRole("button", { name: "Close the draft", exact: true })
    .click();
  await page
    .locator(".sheet .primary")
    .filter({ hasText: /^Close$/ })
    .click();
  await page.getByRole("heading", { name: "draft results." }).waitFor();
  const [earCalls] =
    await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author='fixture' AND undone_at IS NULL AND hidden_at IS NULL`;
  assert.equal(earCalls.n, 3);
  assert.equal(
    await page
      .locator(".draft-columns section")
      .first()
      .locator(".draft-picks a")
      .count(),
    earCalls.n,
  );
  await page.getByText(/Closed by Test viewer/).waitFor();
  await capture("draft-closed-results");
  const [ruleCalls] =
    await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author='rules'`;
  assert.equal(ruleCalls.n, 2);
  const [links] =
    await db`SELECT count(*)::int AS n FROM control.showcase_call_rule`;
  assert.equal(links.n, 4);
  await page.getByRole("button", { name: "See rules", exact: true }).click();
  await capture("draft-backed-rules", true);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  // The next Friday opens on last week's results before the new tray.
  const previous = earlierWeek(week);
  await db`UPDATE control.showcase_draft SET week_start=${previous} WHERE week_start=${week}`;
  await db`UPDATE control.showcase_call SET week_start=${previous},draft_week=${previous} WHERE draft_week=${week}`;
  await freezeDraft(db, week, songs, () => new Date(`${week}T12:00:00Z`));
  await db`UPDATE control.showcase_draft SET closes_at=now()+interval '1 day' WHERE week_start=${week}`;
  await page.goto(origin + "/draft");
  await page.getByRole("heading", { name: "last week's calls." }).waitFor();
  await capture("draft-results-first");
  await page.getByRole("button", { name: "See rules", exact: true }).click();
  const previousTray = page
    .getByRole("link", { name: "See the tray", exact: true })
    .first();
  assert.equal(
    await previousTray.getAttribute("href"),
    `/draft?tray=${previous}`,
  );
  await previousTray.click();
  await page.getByRole("heading", { name: "friday tray." }).waitFor();
  await page.getByRole("link", { name: "See results", exact: true }).click();
  await page.getByRole("heading", { name: "last week's calls." }).waitFor();
  await page
    .getByRole("link", { name: "Open this draft", exact: true })
    .click();
  await page.getByRole("heading", { name: "friday draft." }).waitFor();
  // Another screen closes the draft while a cover's confirmation is open.
  await page.waitForTimeout(400);
  await page.locator(".draft-tray button").first().click();
  await page
    .getByRole("heading", { name: "Call Night tide by Test recording?" })
    .waitFor();
  await db`UPDATE control.showcase_draft SET closed_at=now(),close_key='other-screen' WHERE week_start=${week}`;
  await page.getByRole("button", { name: "Call it", exact: true }).click();
  await capture("draft-closed-refusal", true);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await db`TRUNCATE control.showcase_call_rule,control.showcase_rule,control.showcase_call,control.showcase_draft CASCADE`;
  await freezeDraft(
    db,
    week,
    songs.map((song) => ({ ...song, entered_lists: 0 })),
    () => new Date(`${week}T12:00:00Z`),
  );
  await db`UPDATE control.showcase_draft SET closes_at=now()+interval '1 day' WHERE week_start=${week}`;
  await page.goto(origin + "/draft?board=1");
  const reducedCover = page.locator(".draft-tray button").first();
  assert.equal(
    await reducedCover.evaluate(
      (el) => getComputedStyle(el).transitionProperty,
    ),
    "opacity",
  );
  const reducedDrag = await page.evaluateHandle(() => new DataTransfer());
  await reducedCover.dispatchEvent("dragstart", { dataTransfer: reducedDrag });
  assert.equal(
    await reducedCover.evaluate((el) => getComputedStyle(el).transform),
    "none",
  );
  await reducedCover.dispatchEvent("dragend");
  await reducedCover.focus();
  await page.keyboard.press("Space");
  await page
    .getByRole("heading", { name: "Call Night tide by Test recording?" })
    .waitFor();
  await page.getByRole("button", { name: "Not now", exact: true }).click();
  await page.getByRole("button", { name: "See rules", exact: false }).click();
  await page
    .getByRole("button", { name: "Back rule", exact: true })
    .last()
    .click();
  await page
    .locator(".draft-columns .draft-rule-preview")
    .filter({ hasText: "new song" })
    .waitFor();
  await page
    .getByRole("button", { name: "Close the draft", exact: true })
    .click();
  await page
    .locator(".sheet .primary")
    .filter({ hasText: /^Close$/ })
    .click();
  await page.getByRole("heading", { name: "draft results." }).waitFor();
  await page.getByRole("button", { name: "See rules", exact: true }).click();
  await page.getByText("no match this week", { exact: true }).waitFor();
  await capture("draft-no-match", true);
  await page.getByRole("link", { name: "See the tray", exact: true }).click();
  await page.getByRole("heading", { name: "friday tray." }).waitFor();
  await capture("draft-frozen-tray");
  await writeFile(
    path.join(out, `draft-density-${size}.json`),
    JSON.stringify(measurements, null, 2),
  );
  await db`TRUNCATE control.showcase_call_rule,control.showcase_rule,control.showcase_call,control.showcase_draft CASCADE`;
}
