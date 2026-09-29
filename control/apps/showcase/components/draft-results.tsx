"use client";
import { LocalTime } from "./local-time";
import { useState } from "react";
import { ruleListOrder, type DraftRule } from "../lib/draft";
import { type SavedCall } from "../lib/calls";
import type { CallRead } from "../server/call-reads";
import { Art } from "./movers";
import { Sheet } from "./sheet";
export function DraftResults({
  calls,
  observations,
  rules,
  matched,
  next,
  closedAt,
  closedBy,
  week,
}: {
  calls: SavedCall[];
  observations: CallRead | null;
  rules: DraftRule[];
  matched: string[];
  next: boolean;
  closedAt: string | null;
  closedBy: string | null;
  week: string;
}) {
  const [showRules, setShowRules] = useState(false);
  return (
    <section className="draft-room">
      <h1>{next ? "last week's picks." : "Weekly picks results."}</h1>
      <p>
        {closedBy ? (
          <>
            Closed by {closedBy} ·{" "}
            {closedAt ? (
              <LocalTime at={closedAt} relativeDay />
            ) : (
              "Time unavailable"
            )}
          </>
        ) : (
          "closed on schedule"
        )}
        .{!next && " Pick again next week."}
      </p>
      <div className="draft-columns">
        {[false, true].map((rule) => (
          <section key={String(rule)}>
            <h2>{rule ? "rules" : "your picks"}</h2>
            <div className="draft-picks">
              {calls
                .filter(
                  (c) =>
                    (c.author === "rules") === rule &&
                    !c.undone_at &&
                    !c.hidden_at,
                )
                .map((c) => (
                  <a
                    key={c.id}
                    href={`/songs/picks/${c.id}`}
                    aria-label="Open pick"
                  >
                    <Art
                      sharedLayout={false}
                      song={c.song_key}
                      title="Picked song"
                    />
                    {observations?.rows.some(
                      (r) => r.id === c.id && BigInt(r.total) > 0n,
                    ) && <span>new</span>}
                  </a>
                ))}
            </div>
          </section>
        ))}
      </div>
      {!calls.length && (
        <p>
          No picks this week.{" "}
          <a href="/songs?view=rising">Open Rising now</a>
        </p>
      )}
      {calls.length > 0 && !observations && (
        <p>
          Places load when the platform answers.{" "}
          <a
            href={
              next ? "/songs?view=friday" : "/songs?view=friday&board=1"
            }
          >
            Retry
          </a>
        </p>
      )}
      <button onClick={() => setShowRules(true)}>See rules</button>
      {next ? (
        <a className="primary" href="/songs?view=friday&board=1">
          Open this week&apos;s picks
        </a>
      ) : (
        <a className="primary" href="/songs?view=picks">
          Open picks
        </a>
      )}
      {showRules && (
        <Sheet
          className="call-sheet"
          title="Rules"
          close={() => setShowRules(false)}
        >
          {!rules.length && (
            <p>No rules yet. Back an example rule to start.</p>
          )}
          {rules.length > 0 && <p>Lists, in order: {ruleListOrder}</p>}
          {rules.map((r) => (
            <article key={r.id} data-card className="draft-rule">
              <p>{r.title}</p>
              {!matched.includes(r.id) && <p>no match this week</p>}
              <a data-primary href={`/songs?view=friday&tray=${week}`}>
                See the selection
              </a>
            </article>
          ))}
          {!rules.length && (
            <a className="primary" href={`/songs?view=friday&tray=${week}`}>
              See the selection
            </a>
          )}
        </Sheet>
      )}
    </section>
  );
}
