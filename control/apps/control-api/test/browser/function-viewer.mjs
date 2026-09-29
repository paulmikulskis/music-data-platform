async (page) => {
  const base = "http://127.0.0.1:18409";
  const source = "/functions/fixture_playlist";
  const assert = (value, message) => {
    if (!value) throw Error(message);
  };
  const overflow = async () =>
    page.evaluate(() => ({
      width: innerWidth,
      scroll: document.documentElement.scrollWidth,
    }));
  const results = { sizes: {}, viewports: [], density: [] };
  const service = "http://127.0.0.1:18408";
  const fixture = await page.request.get(
    service + "/v1/functions/fixture_playlist",
  );
  const full = await fixture.json();
  assert(full.receipts.length === 20, "Receipt fixture has no run history");
  assert(
    full.receipts[0][0].loads[0].rows_inserted === "4812",
    "Receipt fixture has no landed rows",
  );
  const builds = async () =>
    (
      await (
        await page.request.get(service + "/__fixture/receipt-builds")
      ).json()
    ).count;
  const beforeFirstPaint = await builds();
  const firstPaint = await page.request.get(base + source);
  assert(firstPaint.status() === 200, "First paint failed");
  assert(
    !(await firstPaint.text()).includes("Fixture receipt:"),
    "First paint includes receipt data",
  );
  assert(
    (await builds()) === beforeFirstPaint,
    "First paint builds receipts upstream",
  );
  await page.request.get(base + source + "/part/live");
  assert(
    (await builds()) === beforeFirstPaint,
    "Live poll builds receipts upstream",
  );
  const receipts = await page.request.get(base + source + "/part/receipts");
  assert(
    (await receipts.text()).includes("Fixture receipt: 4812 rows."),
    "Receipt part loses the real receipt",
  );
  assert(
    (await builds()) === beforeFirstPaint + 1,
    "Receipt part must build only its selected run",
  );
  results.receipts = {
    fixtureRuns: full.receipts.length,
    firstPaintBuilds: 0,
    liveBuilds: 0,
    partBuilds: 1,
  };
  const density = () =>
    page.evaluate(() => {
      const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
      );
      const words = [];
      const prose = [];
      const proseExclusions =
        "header,nav,[data-breadcrumb],table,code,.copy-chip,[data-density-value],time";
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const element = node.parentElement;
        if (!element || element.closest("script,style,.sr-only,[hidden]"))
          continue;
        let hidden = false;
        for (let parent = element; parent; parent = parent.parentElement) {
          if (
            parent.tagName === "DETAILS" &&
            !parent.open &&
            !parent.querySelector(":scope > summary")?.contains(node)
          )
            hidden = true;
          if (
            getComputedStyle(parent).visibility === "hidden" ||
            getComputedStyle(parent).display === "none"
          )
            hidden = true;
        }
        if (hidden) continue;
        const range = document.createRange();
        range.selectNode(node);
        const rect = range.getBoundingClientRect();
        if (
          rect.width &&
          rect.height &&
          rect.top >= 0 &&
          rect.bottom <= innerHeight
        ) {
          const found =
            node.textContent.match(
              /[\p{L}\p{N}]+(?:[’':.,-][\p{L}\p{N}]+)*/gu,
            ) ?? [];
          words.push(...found);
          if (!element.closest(proseExclusions))
            prose.push(
              ...found.filter(
                (word) => !/^\d[\d.,]*(?:[KMB%]|[hdms])?$/i.test(word),
              ),
            );
        }
      }
      const text = words.join(" ");
      return {
        width: innerWidth,
        fullWords: words.length,
        proseWords: prose.length,
        text,
        proseText: prose.join(" "),
      };
    });
  const pages = [
    source,
    "/functions",
    ...[
      "live",
      "rows",
      "targets",
      "receipts",
      "schema",
      "settings",
      "cursors",
    ].map((p) => `${source}/part/${p}`),
  ];
  const links = new Set();
  for (const path of pages) {
    const response = await page.request.get(base + path);
    assert(response.status() === 200, `${path}: ${response.status()}`);
    const body = await response.text();
    results.sizes[path] = unescape(encodeURIComponent(body)).length;
    assert(
      unescape(encodeURIComponent(body)).length <=
        (path.endsWith("/live")
          ? 30000
          : path.includes("/part/")
            ? 400000
            : 200000),
      `Too large: ${path}`,
    );
    if (path === source)
      assert(!body.includes("<pre>"), "First paint contains raw JSON");
    if (path.includes("/part/"))
      assert(!body.includes("failure-banner"), "Parts contain a banner");
    for (const match of body.matchAll(/href="([^"#][^"]*)"/g)) {
      const value = match[1].replaceAll("&amp;", "&");
      if (value.startsWith("/") && !value.startsWith("//"))
        links.add(base + value);
      else if (value.startsWith("?"))
        links.add(base + path.split("?")[0] + value);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(base + source);
  await page.locator(".latest-table tbody tr").first().waitFor();
  assert(
    (await page.locator("#target-strip .cell").count()) === 36,
    "Target population is not 36",
  );
  assert(
    (await page.locator("#target-strip details,#target-strip form").count()) ===
      0,
    "Interactive nesting in target strip",
  );
  assert(
    (await page.locator('#target-strip [data-platform="spotify"]').count()) ===
      36,
    "Mixed target platforms",
  );
  assert(
    (await page
      .locator("[data-strip-filter][data-count]")
      .evaluateAll((nodes) =>
        nodes.reduce((sum, n) => sum + Number(n.dataset.count), 0),
      )) === 36,
    "Legend does not reconcile",
  );
  assert((await page.locator(".label-chips").count()) === 0, "Repeated labels");
  assert(
    (await page.locator('[data-strip-filter][data-count="0"]').count()) === 0,
    "Zero counts clutter the legend",
  );
  assert(
    (await page.locator(".function-meta").innerText()) ===
      "Daily · Rights: no learning, no resale ⓘ",
    "Visible metadata contains layer or scope",
  );
  const visible = await page.locator("body").innerText();
  assert(
    !/stale|Delivery health|Grey ticks|never probed|Run sample|View functions →/.test(
      visible,
    ),
    "Banned viewer copy",
  );
  assert(
    (visible.match(/\d+ of \d+ targets/g) || []).length === 1,
    "Repeated coverage",
  );
  assert((visible.match(/Rights:/g) || []).length === 1, "Repeated rights");
  await page.locator("#target-strip .cell").first().hover();
  await page.locator("#hover-card").waitFor();
  const targetHover = await page.locator("#hover-card").innerText();
  assert(
    /Fixture list/.test(targetHover) &&
      /Read|Unchanged|Failed|Gone|Parked|Not read yet/.test(targetHover) &&
      /ago/.test(targetHover),
    "Incomplete target hover",
  );
  assert(targetHover.split(/\s+/).length <= 30, "Long target hover");
  assert(
    (targetHover.match(/\d+/g) || []).length <= 4,
    "Too many hover numbers",
  );
  assert(
    (await page.locator('#hover-card a[href^="/targets/"]').count()) === 1,
    "Missing target next step",
  );
  await page.screenshot({
    path: "ops/evidence/h-function-viewer/function-desktop.png",
  });
  await page.keyboard.press("Escape");
  await page.locator("#target-strip .cell").first().focus();
  await page.keyboard.press("ArrowRight");
  assert(
    await page
      .locator("#target-strip .cell")
      .nth(1)
      .evaluate((n) => n === document.activeElement),
    "Arrow navigation failed",
  );
  await page.keyboard.press("End");
  assert(
    await page
      .locator("#target-strip .cell")
      .last()
      .evaluate((n) => n === document.activeElement),
    "End navigation failed",
  );
  await page.keyboard.press("Home");
  await page.keyboard.press("Escape");
  assert(
    (await page.locator("#hover-card").count()) === 0,
    "Escape did not hide hover",
  );
  await page.keyboard.press("Enter");
  await page.waitForURL("**/targets/*");
  await page.goto(base + source);
  await page.locator(".function-menu summary").focus();
  await page.keyboard.press("Enter");
  assert(
    (await page.locator(".function-menu").innerText()).includes(
      "Layer: bronze",
    ),
    "Layer is missing from the menu",
  );
  let posts = 0;
  const countPosts = (request) => {
    if (request.method() === "POST") posts++;
  };
  page.on("request", countPosts);
  await page
    .locator(".function-menu button")
    .filter({ hasText: "Probe" })
    .hover();
  await page.locator("#hover-card").waitFor();
  assert(
    (await page.locator("#hover-card").innerText()).includes(
      "Makes a few live requests. Publishes nothing.",
    ),
    "Wrong probe tooltip",
  );
  assert(posts === 0, "Hover fired a POST");
  page.off("request", countPosts);
  await page.keyboard.press("Escape");
  await page.locator("[data-rows-tab]").nth(1).focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(
    () =>
      document
        .querySelector("[data-preview]")
        ?.getAttribute("data-column-key") === "raw.fixture_items",
  );
  await page.locator(".columns-choice summary").click();
  await page.locator('[data-column-name="detail_0"]').check();
  assert(
    (await page.locator("[data-column-summary]").innerText()).includes(
      "6 of 31",
    ),
    "Column count did not update",
  );
  await page.reload();
  await page.locator("[data-preview]").waitFor();
  await page.locator("[data-rows-tab]").nth(1).click();
  await page.waitForFunction(
    () =>
      document
        .querySelector("[data-preview]")
        ?.getAttribute("data-column-key") === "raw.fixture_items",
  );
  assert(
    await page.locator('[data-column-name="detail_0"]').isChecked(),
    "Column choice was not saved",
  );
  await page.locator(".columns-choice summary").click();
  await page.locator('[data-column-name="detail_0"]').uncheck();
  await page.locator(".columns-choice summary").click();
  await page.locator("[data-row-open]").first().click();
  assert(
    (await page.locator('dialog[open] a[href^="/runs/"]').count()) > 0,
    "Row sheet loses run link",
  );
  assert(
    (await page.locator('dialog[open] a[href^="/targets/"]').count()) > 0,
    "Row sheet loses target link",
  );
  await page.keyboard.press("Escape");
  await page.locator('[data-strip-filter="all"]').click();
  await page.locator("[data-target-row]").first().waitFor();
  assert(
    (await page.locator("[data-target-row]:visible").count()) === 25,
    "Drill-down not bounded",
  );
  assert(
    (await page.locator("[data-target-state] option").count()) === 7,
    "Drill-down hides a target state",
  );
  await page.locator("[data-target-state]").selectOption("failed");
  await page.getByText("No matching targets.", { exact: false }).waitFor();
  await page.locator("[data-target-state]").selectOption("all");
  await page.locator("[data-target-all]").waitFor();
  await page.locator("[data-target-all]").click();
  assert(
    (await page.locator("[data-target-row]:visible").count()) === 36,
    "Show all failed",
  );
  await page.locator("[data-target-filter]").fill("001");
  assert(
    (await page.locator("[data-target-row]:visible").count()) === 1,
    "Target filter failed",
  );
  await page.keyboard.press("Escape");
  await page.route("**/part/receipts", (route) =>
    route.fulfill({ status: 503, body: "fixture failure" }),
  );
  await page.locator('details[data-part="receipts"] summary').click();
  await page
    .getByText("Couldn't load receipts. Try again.", { exact: true })
    .waitFor();
  assert(
    (await page
      .locator('[data-part="receipts"] button')
      .filter({ hasText: "Retry" })
      .count()) === 1,
    "Missing lazy retry",
  );
  assert(
    (await page
      .locator('[data-part="receipts"] a')
      .filter({ hasText: "Open run" })
      .count()) === 1,
    "Missing lazy run link",
  );
  await page.unroute("**/part/receipts");
  await page
    .locator('[data-part="receipts"] button')
    .filter({ hasText: "Retry" })
    .click();
  await page.locator("[data-receipt-run]").first().waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await page.goto(base + source);
    await page.locator("[data-preview]").waitFor();
    const measured = await density();
    results.density.push(measured);
    const size = await overflow();
    assert(size.scroll <= size.width, `Overflow at ${width}`);
    results.viewports.push(size);
    await page.locator('[data-strip-filter="all"]').click();
    await page.locator("[data-target-row]").first().waitFor();
    assert((await overflow()).scroll <= width, "Drill-down overflow");
    await page.keyboard.press("Escape");
    await page.locator(".failure-group details summary").click();
    assert((await overflow()).scroll <= width, "Banner overflow");
    await page.goto(base + "/functions");
    assert((await overflow()).scroll <= width, "List overflow");
    const emptyCards = page
      .locator("[data-fn-card]")
      .filter({ hasText: "No rows in the last 14 days" });
    assert((await emptyCards.count()) > 0, "No empty daily history fixture");
    assert(
      (await emptyCards.locator(".daily-bars").count()) === 0,
      "Empty daily history still renders flat bars",
    );
    assert(
      (await page.locator(".failure-group").count()) === 1,
      "Failures not grouped",
    );
    assert(
      (await page.locator(".failure-group h2").innerText()).includes("× 2"),
      "Missing failure count",
    );
    assert(
      !/Traceback|plpy|compiled code at/.test(
        await page.locator(".failure-group").innerText(),
      ),
      "Visible traceback",
    );
    if (width === 1440)
      await page.screenshot({
        path: "ops/evidence/h-function-viewer/functions-desktop.png",
      });
    await page.locator("[data-fn-filter]").fill("fixture_playlist");
    assert(
      (await page.locator("[data-fn-card]:visible").count()) === 1,
      "Function filter failed",
    );
  }
  for (const href of links) {
    const response = await page.request.get(href);
    assert(
      response.status() === 200,
      `Broken link ${href.slice(base.length)}: ${response.status()}`,
    );
  }
  for (const part of [
    "live",
    "rows",
    "targets",
    "receipts",
    "schema",
    "settings",
    "cursors",
  ]) {
    const response = await page.request.get(`${base}${source}?open=${part}`);
    assert(response.status() === 200, `No-JS ${part} failed`);
  }
  const mobile = await page
    .context()
    .browser()
    .newContext({
      viewport: { width: 390, height: 844 },
      isMobile: true,
      hasTouch: true,
    });
  const phone = await mobile.newPage();
  await phone.goto(base + source);
  await phone.locator("#run-chart [data-run-id]").first().focus();
  await phone.locator("#hover-card").waitFor();
  assert(
    (await phone.locator("#hover-card").innerText()).includes("Run "),
    "Missing run hover",
  );
  assert(
    (await phone.locator('#hover-card a[href^="/runs/"]').count()) === 1,
    "Missing run next step",
  );
  await phone.screenshot({
    path: "ops/evidence/h-function-viewer/function-mobile.png",
  });
  await mobile.close();
  results.links = links.size;
  results.densityVerdict = results.density.every(
    (d) => d.proseWords <= (d.width === 390 ? 40 : 70),
  )
    ? "PASS"
    : "FAIL";
  assert(
    results.densityVerdict === "PASS",
    "Prose word limit exceeded: " + JSON.stringify(results.density),
  );
  return results;
}
