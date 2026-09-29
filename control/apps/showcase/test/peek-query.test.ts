import { describe, expect, it } from "vitest";
import { peekQuery } from "../server/peek-query";
import { traceLink } from "../lib/trace";
import { injectBackBar, locationFor } from "../server/console-policy";
import { viewerPath } from "../lib/proof-link";

describe("reviewed previews", () => {
  it("refuses unknown relations, tenant tables and unreviewed fields", () => {
    for (const relation of [
      "tenant_sentinel_marts.mart_chart_history",
      "raw.comments",
      "marts.mart_playlist_profile; SELECT 1",
      "marts.unknown",
    ])
      expect(peekQuery({ relation })).toBeNull();
    for (const key of [
      "creator",
      "commenter",
      "description",
      "owner_id",
      "tenant_id",
      "song_key",
    ])
      expect(
        peekQuery({
          relation: "marts.mart_playlist_profile",
          filters: { [key]: "sentinel" },
        }),
      ).toBeNull();
  });
  it("binds values and quotes the same values for the Workbench", () => {
    const value = "song'\\</textarea>; DROP TABLE x;--";
    const query = peekQuery({
      relation: "marts.mart_playlist_profile",
      filters: { playlist_id: value },
    });
    expect(query?.sql).toContain('a."playlist_id"=$1');
    expect(query?.sql).not.toContain(value);
    expect(query?.values).toEqual([value]);
    expect(query?.prefill).toContain(
      "E'song''\\\\</textarea>; DROP TABLE x;--'",
    );
    expect(query?.sql).toMatch(/LIMIT 5$/);
    expect(query?.spec.columns).toHaveLength(4);
  });
  it("keeps the exact chart position from the song evidence", () => {
    const query = peekQuery({
      relation: "marts.mart_shazam_chart_daily",
      filters: { chart: "city", chart_date: "2026-09-27", position: 7 },
    });
    expect(query?.sql).toContain('a."position"=$3');
    expect(query?.values).toEqual(["city", "2026-09-27", 7]);
  });

  it("preserves the originating trace and rejects redirect tricks", () => {
    const back = traceLink(
      { entry: "home.playlist_adds", song: "song'&<", ranking: "one" },
      "/",
    );
    expect(viewerPath(back)).toBe(back);
    for (const path of [
      "//evil.invalid",
      "/%2e%2e/ops",
      "/sources/../ops",
      "/sources/%2f%2fevil",
      "/sources\\evil",
    ])
      expect(viewerPath(path)).toBeNull();
    expect(
      locationFor(
        "https://evil.invalid/workbench",
        "http://control.invalid",
        "https://showcase.invalid",
      ),
    ).toBeNull();
    expect(
      locationFor(
        "/%2e%2e/workbench",
        "http://control.invalid",
        "https://showcase.invalid",
      ),
    ).toBeNull();
    const html = injectBackBar(
      '<html><body><form method="post" action="/workbench"><button>Start</button></form></body></html>',
      back,
      "",
      "signed-context",
    );
    expect(html).toContain('action="/workbench?showcase_proof=signed-context"');
    expect(html).toContain("&amp;");
  });
});
