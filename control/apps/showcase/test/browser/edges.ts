import assert from "node:assert/strict";
import path from "node:path";
import { writeFile } from "node:fs/promises";
import type { Page } from "@playwright/test";
import type { Sql } from "postgres";
import { signProof } from "../../server/proof-token";
import { navigation } from "./navigation";
import { keys } from "./fixtures";
import { density, overlay, densityPolicy } from "./density.mjs";

export async function seedEdges(wh: Sql) {
  await wh`INSERT INTO marts.mart_search_index(object_key,kind,display_text,context,aliases,last_seen,learning_eligible,resale_permitted,source_keys)
    VALUES ('source:sp_playlist','source','Spotify reader','{"key":"sp_playlist"}','[]',now(),false,false,'["sp_playlist"]')`;
  await wh`UPDATE marts.mart_arrivals_current SET markets='["AU","BR","JP","MX"]', market_count=4 WHERE song_key='fixture-catalog-1'`;
  await wh`DELETE FROM intermediate.int_cluster_shazam__daily WHERE song_key='fixture-catalog-1'`;
  await wh`INSERT INTO intermediate.int_cluster_shazam__daily(song_key,chart_date,country)
    SELECT 'fixture-catalog-1', CURRENT_DATE - CASE WHEN market='AU' THEN 10 ELSE 0 END, market
    FROM unnest(ARRAY['AU','BR','JP','MX']) market`;
  await wh`DELETE FROM intermediate.int_cluster_entries__daily WHERE song_key='fixture-catalog-1'`;
  await wh`INSERT INTO intermediate.int_cluster_entries__daily(song_key,day,platform,list_kind,market)
    SELECT 'fixture-catalog-1', CURRENT_DATE, 'shazam','chart',market FROM unnest(ARRAY['AU','BR','JP','MX']) market`;
  await wh`DELETE FROM intermediate.int_song_windows__daily WHERE day=CURRENT_DATE`;
  await wh`INSERT INTO intermediate.int_song_windows__daily(day,family,window_days) VALUES (CURRENT_DATE,'shazam',7),(CURRENT_DATE,'playlists',7)`;
  await wh`DELETE FROM marts.mart_early_signals_current WHERE family='streams'`;
  await wh`INSERT INTO marts._build(relation,cycle_id,close_no,built_at)
    SELECT 'marts.mart_playlist_events',cycle_id,close_no,built_at FROM marts._build WHERE relation='marts.mart_song_day'
    ON CONFLICT(relation) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,close_no=EXCLUDED.close_no,built_at=EXCLUDED.built_at`;
}
export async function edgesWalk(
  page: Page,
  db: Sql,
  origin: string,
  out: string,
  size: string,
) {
  const measurements: Record<string, unknown> = {};
  const shots = async (name: string) => {
    await page.waitForTimeout(750);
    if (!name.startsWith("engine")) {
      const sheet = await page.locator("dialog[open]").count();
      if (sheet) {
        const reading = await page.evaluate(overlay, {
          kind: "sheet",
          policy: densityPolicy,
        });
        measurements[name] = reading;
        assert(
          reading.words <= 80 && !reading.banned.length,
          name + ": " + reading.text,
        );
      } else {
        const reading = await page.evaluate(density, densityPolicy);
        measurements[name] = reading;
        if (size.startsWith("390"))
          assert(
            reading.words <= 40 &&
              reading.numbers <= 3 &&
              !reading.banned.length,
            name + ": " + reading.text,
          );
      }
    }
    await page.screenshot({
      path: path.join(out, `edges-${name}-${size}.png`),
    });
  };
  await page.goto(origin + "/today");
  // The real console receives an unrelated critical alert. Its operator page keeps it.
  const [alert] =
    await db`INSERT INTO control.alert(class,severity,subject_type,subject_id)
    VALUES ('cadence_failed','critical','fixture','edge-test') RETURNING id`;
  try {
    const token = signProof({
      handle: "fixture",
      level: "source",
      song: "",
      back: "/songs?view=places",
    });
    await page.waitForTimeout(1100);
    await page.goto(
      `${origin}/functions/browser_fixture?showcase_proof=${encodeURIComponent(token)}`,
    );
    await navigation(page, origin + "/songs?view=places");
    assert((await page.locator('form[method="post"]').count()) > 0);
    assert((await page.locator("body").innerText()).includes("cadence failed"));
    await shots("engine-source");
    const [run] =
      await db`SELECT id FROM control.run WHERE work_key='browser-fixture-poll'`;
    const proofToken = signProof({
      handle: "fixture",
      song: "",
      level: "row",
      back: "/songs?view=places",
    });
    await page.goto(
      `${origin}/runs/${run.id}?showcase_proof=${encodeURIComponent(proofToken)}`,
    );
    await navigation(page, origin + "/songs?view=places");
    assert((await page.locator("body").innerText()).includes("cadence failed"));
    await shots("engine-proof");
    await page.goto(
      `${origin}/ops?showcase_proof=${encodeURIComponent(proofToken)}`,
    );
    await page
      .getByRole("button", { name: "Retry cycle", exact: true })
      .waitFor();
    await navigation(page, origin + "/songs?view=places");
    await shots("engine-admin");
    await page
      .getByRole("button", { name: "Retry cycle", exact: true })
      .scrollIntoViewIfNeeded();
    await shots("engine-retry");
    await page.getByRole("link", { name: "Back to showcase" }).click();
    await page.waitForURL("**/songs?view=places");
    await page
      .getByRole("button", { name: "Reached 3 new markets.", exact: true })
      .click();
    assert.equal(
      await page
        .locator(".market-list strong")
        .filter({ hasText: /^New$/ })
        .count(),
      3,
    );
    assert.equal(
      await page
        .locator(".market-list strong")
        .filter({ hasText: "Already there" })
        .count(),
      0,
    );
    await shots("markets");
  } finally {
    await db`DELETE FROM control.alert WHERE id=${alert.id}`;
  }
  await page.goto(origin + "/songs?view=places");
  await shots("places");
  await page
    .getByRole("button", { name: "Collection. Open details", exact: true })
    .click();
  assert(
    await page.getByRole("dialog").locator("time[data-local-time]").count(),
  );
  await shots("live-sheet");
  // A daily failure remains unresolved after acknowledgment, even with an idle runner.
  const failedAt = new Date(Date.now() - 3600000).toISOString();
  const [daily] =
    await db`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id)
    VALUES ('daily','global','core-scheduled-edge-failure') RETURNING id`;
  const [failure] =
    await db`INSERT INTO control.alert(class,severity,subject_type,subject_id,opened_at,acknowledged_by)
    VALUES ('cadence_failed','critical','cycle',${daily.id},${failedAt},'fixture') RETURNING id`;
  try {
    await page.getByRole("link", { name: "Check collection →" }).click();
    await page.getByRole("heading", { name: "collection status." }).waitFor();
    await page
      .getByText("Collection needs attention.", { exact: true })
      .waitFor();
    await page.getByText("Next collection from", { exact: false }).waitFor();
    assert.equal(await page.locator(`time[datetime="${failedAt}"]`).count(), 1);
    assert.equal(
      await page.locator('a[href="/status"], form[method="post"]').count(),
      0,
    );
    assert(
      !/Traceback|Retry cycle|Waiting for the next read/.test(
        await page.locator("main").innerText(),
      ),
    );
    await shots("status");
    await db`UPDATE control.alert SET resolved_at=now() WHERE id=${failure.id}`;
    await page.reload();
    await page
      .getByText("Waiting for the next read.", { exact: true })
      .waitFor();
    assert.equal(
      await page
        .getByText("Collection needs attention.", { exact: true })
        .count(),
      0,
    );
  } finally {
    await db`DELETE FROM control.alert WHERE id=${failure.id}`;
    await db`DELETE FROM control.cycle WHERE id=${daily.id}`;
  }

  await page.goto(origin + "/rising");
  assert.equal(
    await page.getByRole("button", { name: "Plays", exact: true }).count(),
    0,
  );
  await page.getByRole("button", { name: "All", exact: true }).waitFor();
  await shots("plays-building-history");
  await page.goto(origin + "/holdings");
  await page.getByRole("heading", { name: "what’s collected." }).waitFor();
  await shots("holdings");
  await page.goto(origin + "/s/proof/source/sp_playlist?from=%2Fsignal");
  await page.getByText(/36 playlists tracked at/).waitFor();
  assert(!(await page.locator("main").innerText()).includes("139"));
  await shots("source-proof");
  await page.goto(origin + "/library");
  await page.getByRole("searchbox").fill("Spotify reader");
  await page
    .getByRole("button", { name: "Open Spotify reader", exact: true })
    .click();
  await page.getByText(/36 playlists tracked at/).waitFor();
  assert.equal(
    await page.locator('dialog[open] a[href^="/functions"]').count(),
    0,
  );
  await shots("library-source");
  await page
    .getByRole("link", { name: "See source details →", exact: true })
    .click();
  await page.waitForURL("**/s/proof/source/sp_playlist?*");
  await page.goto(origin + "/s/song/" + encodeURIComponent(keys[0]));
  await page
    .getByRole("button", { name: "Daily adds", exact: false })
    .first()
    .waitFor();
  await shots("song");
  await page
    .getByRole("button", { name: "Daily adds", exact: false })
    .first()
    .click();
  await shots("daily-adds");
  // End an RSC stream after its route tree arrives but before its page arrives.
  // An abort before any response makes Next reload automatically; a partial stream
  // reaches the route error boundary and exercises its Retry button.
  for (const interruption of ["page", "metadata"] as const) {
    await page.goto(origin + "/songs?view=places");
    let aborted = false;
    await page.route("**/rising*", async (route) => {
      if (!aborted && route.request().headers().rsc === "1") {
        aborted = true;
        const response = await route.fetch();
        const body = await response.text();
        const root = body.split("\n").find((line) => line.startsWith("0:"));
        const pageChunk = root?.match(/"children":\["\$L([a-f0-9]+)"/)?.[1];
        assert(pageChunk, "The route tree carries a pending page.");
        const end =
          interruption === "page"
            ? body.indexOf("\n" + pageChunk + ":")
            : body.indexOf("\n", body.indexOf(root!));
        assert(end > 0, "The page arrives after its route and metadata.");
        await route.fulfill({ response, body: body.slice(0, end + 1) });
      } else {
        await route.continue();
      }
    });
    await page
      .getByRole("navigation", { name: "Views" })
      .getByRole("link", { name: "Rising" })
      .click();
    await page
      .getByRole("heading", { name: "origin is out of reach." })
      .waitFor();
    assert(aborted, "The in-app request was aborted.");
    await page.getByRole("navigation", { name: "Views" }).waitFor();
    await page.getByRole("button", { name: "Open menu" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Close" })
      .click();
    await shots("retry-" + interruption);
    const document = page.waitForRequest(
      (request) =>
        request.isNavigationRequest() &&
        new URL(request.url()).pathname === "/rising",
    );
    await page.getByRole("button", { name: "Retry ↻" }).click();
    await document;
    await page.getByRole("heading", { name: "who’s moving." }).waitFor();
    await shots("recovered-" + interruption);
    await page.unroute("**/rising*");
  }
  await writeFile(
    path.join(out, `edges-density-${size}.json`),
    JSON.stringify(measurements, null, 2),
  );
}
