import { room } from "../../server/room";
import { Shell } from "../../components/shell";
import { DraftBoard } from "../../components/draft-board";
import { DraftResults } from "../../components/draft-results";
import {
  draftWeek,
  draftWaiting,
  draftWindowPassed,
  nextDraftWaiting,
} from "../../lib/draft";
import { callWeek, earlierWeek } from "../../lib/calls";
import { ensureDraft, readDraftNames } from "../../server/draft-reads";
import { readDraft, readRules, draftCloser } from "../../server/draft-store";
import { draftOffers } from "../../server/draft-offers";
import { controlStore, warehouse } from "../../server/clients";
import { budget } from "../../server/read-budget";
import { callsBoard } from "../../server/call-reads";
import { Art } from "../../components/movers";
export const dynamic = "force-dynamic";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ board?: string; tray?: string }>;
}) {
  const current = await room();
  const query = await searchParams;
  const week = callWeek();
  let unavailable = false;
  const opened = await ensureDraft(week).catch(() => {
    unavailable = true;
    return null;
  });
  const trayWeek = draftWeek.safeParse(query.tray);
  const tray = trayWeek.success
    ? await budget.run("light", () => readDraft(controlStore(), trayWeek.data))
    : null;
  const previous =
    !query.board && !query.tray
      ? await budget.run("light", () =>
          readDraft(controlStore(), earlierWeek(week)),
        )
      : null;
  const shown = tray?.closed_at
    ? tray
    : previous?.closed_at
      ? previous
      : opened;
  if (!shown)
    return (
      <Shell handle={current.handle} csrf={current.csrf_token}>
        <section className="draft-room">
          <h1>Weekly picks</h1>
          <p>
            {unavailable
              ? "The selection could not load. Open picks while it is unavailable."
              : draftWindowPassed(week)
                ? nextDraftWaiting(week)
                : budget.runnerState === "busy"
                  ? "The overnight read is still running. Open picks while it finishes."
                  : budget.runnerState === "unknown"
                    ? "The overnight read's status is unavailable. Open picks."
                    : draftWaiting}
          </p>
          <a className="primary" href="/songs?view=picks">
            Open picks
          </a>
        </section>
      </Shell>
    );
  const board = await callsBoard(shown.week_start);
  if (query.tray && shown.closed_at)
    return (
      <Shell handle={current.handle} csrf={current.csrf_token}>
        <section className="draft-room">
          <h1>friday selection.</h1>
          <div className="draft-tray">
            {shown.candidates.map((c) => (
              <a
                key={c.song_key}
                href={`/s/song/${encodeURIComponent(c.song_key)}`}
              >
                <Art
                  sharedLayout={false}
                  song={c.song_key}
                  title="Weekly song"
                />
              </a>
            ))}
          </div>
          <a
            className="primary"
            href={
              shown.week_start === week
                ? "/songs?view=friday&board=1"
                : "/songs?view=friday"
            }
          >
            See results
          </a>
        </section>
      </Shell>
    );
  if (shown.closed_at) {
    const matched = await budget.run(
      "light",
      () =>
        controlStore()`SELECT DISTINCT r.rule_id FROM control.showcase_call_rule r JOIN control.showcase_call c ON c.id=r.call_id WHERE c.draft_week=${shown.week_start}`,
    );
    return (
      <Shell handle={current.handle} csrf={current.csrf_token}>
        <DraftResults
          calls={board.calls}
          observations={board.observations?.value ?? null}
          rules={shown.rules}
          matched={matched.map((r) => String(r.rule_id))}
          next={shown.week_start !== week}
          closedAt={shown.closed_at}
          closedBy={await budget.run("light", () =>
            draftCloser(controlStore(), shown.closed_by),
          )}
          week={shown.week_start}
        />
      </Shell>
    );
  }
  const rules = await budget.run("light", () => readRules(controlStore()));
  const mine = board.calls.filter((c) => c.author === current.handle);
  const names = await budget
    .run("heavy", () =>
      readDraftNames(
        warehouse(),
        shown.candidates.map((c) => c.song_key),
      ),
    )
    .catch(() => []);
  const offers = draftOffers(current, shown, mine);
  return (
    <Shell handle={current.handle} csrf={current.csrf_token}>
      <DraftBoard
        draft={shown}
        names={names}
        rules={rules}
        offers={offers}
        calls={mine}
        csrf={current.csrf_token}
      />
    </Shell>
  );
}
