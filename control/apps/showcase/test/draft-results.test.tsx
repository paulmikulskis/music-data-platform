import { martBuild } from "@mdp/data-sdk";
import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DraftResults } from "../components/draft-results";
import type { SavedCall } from "../lib/calls";
import { build, keys } from "./browser/fixtures";

const call: SavedCall = {
  id: keys[0],
  author: "fixture",
  song_key: keys[0],
  week_start: "2026-09-18",
  submitted_at: "2026-09-18T17:00:00Z",
  undone_at: null,
  hidden_at: null,
  facts: {
    v: 1,
    card: "arrival",
    song_key: keys[0],
    anchors: [
      {
        platform: "apple",
        platform_track_id: "fixture",
        song_key: keys[0],
      },
    ],
    anchors_truncated: false,
    places_shown: null,
    builds: [martBuild.parse(build("mart_arrivals_current"))],
    source_keys: [],
    facts_day: "2026-09-18",
    close_no: "41",
    idempotency_key: keys[0],
    handle: "fixture",
    exp: 0,
    list: "new_entries",
    window_days: 7,
    entered_lists: "2",
    discovery: [],
  },
};

it.each([
  {
    next: true,
    href: "/songs?view=friday",
    heading: "last week&#x27;s picks.",
  },
  {
    next: false,
    href: "/songs?view=friday&amp;board=1",
    heading: "Weekly picks results.",
  },
])(
  "keeps unavailable results on the displayed view: $href",
  ({ next, href, heading }) => {
    const html = renderToStaticMarkup(
      <DraftResults
        calls={[call]}
        observations={null}
        rules={[]}
        matched={[]}
        next={next}
        closedBy={null}
        closedAt="2026-09-18T22:00:00Z"
        week={call.week_start}
      />,
    );
    expect(html).toContain(heading);
    expect(html).toContain(`<a href="${href}">Retry</a>`);
    if (next) expect(html).toContain("Open this week&#x27;s picks");
  },
);

it.each([
  { by: "Test viewer", text: "Time loading" },
  { by: null, text: "closed on schedule" },
])("shows who closes the draft: $text", ({ by, text }) => {
  const html = renderToStaticMarkup(
    <DraftResults
      calls={[]}
      observations={null}
      rules={[]}
      matched={[]}
      next={false}
      closedAt="2026-09-19T22:00:00Z"
      closedBy={by}
      week="2026-09-18"
    />,
  );
  expect(html).toContain(text);
  expect(html).toContain("Pick again next week.");
  expect(html).toContain('href="/songs?view=picks"');
});
