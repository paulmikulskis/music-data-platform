"use client";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import type { DraftName } from "../server/draft-reads";
import { ruleListOrder, type Draft, type DraftRule } from "../lib/draft";
import {
  CALLS_PER_WEEK,
  CALL_WEEK_DAY,
  type CallOffer,
  type SavedCall,
} from "../lib/calls";
import { callMutation } from "./call-it";
import { Sheet } from "./sheet";
import { Art } from "./movers";

const answer = z.object({ error: z.string().optional(), next: z.string() });
export function DraftBoard({
  draft,
  names,
  rules,
  offers,
  calls,
  csrf,
}: {
  draft: Draft;
  names: DraftName[];
  rules: DraftRule[];
  offers: Record<string, CallOffer>;
  calls: SavedCall[];
  csrf: string;
}) {
  const router = useRouter();
  const songName = (key: string) => {
    const name = names.find((n) => n.song_key === key);
    return `${name?.title_text ?? "Title unavailable"} by ${name?.artist_text ?? "artist unavailable"}`;
  };
  const [ear, setEar] = useState(calls);
  const [sheet, setPanel] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryAction, setRetryAction] = useState<"pick" | "back" | "close">(
    "close",
  );
  const [ruleToBack, setRuleToBack] = useState<string | undefined>();
  const closeKey = useRef<string | null>(null);
  const active = useRef(false);
  async function pick(song: string) {
    const offer = offers[song];
    if (!offer || active.current) return;
    active.current = true;
    setBusy(true);
    setSelected(song);
    setRetryAction("pick");
    setDragging(null);
    try {
      const result = await callMutation(csrf, {
        action: "call",
        signed: offer.signed,
      });
      if (result.error === "showcase_link_expired")
        return router.push("/sign-in?reason=ended");
      if (result.call) {
        const call = result.call;
        setEar((items) =>
          items.some((c) => c.id === call.id) ? items : [...items, call],
        );
        setPanel(null);
        setError(null);
        router.refresh();
      } else {
        setError(result.error ?? "call_checking");
        setPanel("error");
      }
    } catch {
      setError("call_checking");
      setPanel("error");
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  async function act(action: "back" | "close", id?: string) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setRetryAction(action);
    setRuleToBack(id);
    closeKey.current ??= crypto.randomUUID();
    try {
      const response = await fetch("/draft/action", {
        signal: AbortSignal.timeout(8000),
        method: "POST",
        headers: { "Content-Type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify(
          action === "back"
            ? { action, week: draft.week_start, id }
            : { action, week: draft.week_start, key: closeKey.current },
        ),
      });
      const result = answer.parse(await response.json());
      if (response.status === 401) return router.push("/sign-in?reason=ended");
      if (result.error) {
        setError(result.error);
        setPanel("error");
      } else {
        setPanel(null);
        router.refresh();
      }
    } catch {
      setError("draft_close_failed");
      setPanel("error");
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="draft-room">
      <h1>Weekly picks</h1>
      <p>Recent music from the scheduled read.</p>
      <div className="draft-columns">
        <section
          className={`draft-ear${dragging ? " receiving" : ""}`}
          aria-label="Ear column"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const song = e.dataTransfer.getData("text/plain");
            if (offers[song]) void pick(song);
          }}
        >
          <h2>
            your picks{" "}
            <span aria-label="weekly slots" className="draft-slots">
              {Array.from({ length: CALLS_PER_WEEK }, (_, i) => (
                <i key={i} className={i < ear.length ? "filled" : ""} />
              ))}
            </span>
          </h2>
          <div className="draft-picks">
            {ear
              .filter((c) => !c.undone_at && !c.hidden_at)
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
                </a>
              ))}
          </div>
          <p>{dragging ? "Drop here" : "Drag a cover here"}</p>
        </section>
        <section>
          <h2>rules</h2>
          {rules
            .filter((r) => r.backed_by)
            .map((r) => (
              <button
                key={r.id}
                className="draft-rule-preview"
                onClick={() => setPanel("rules")}
              >
                {r.title}
              </button>
            ))}
          {!rules.some((r) => r.backed_by) && (
            <button
              className="draft-rule-preview"
              onClick={() => setPanel("rules")}
            >
              Example rules
            </button>
          )}
          <button onClick={() => setPanel("rules")}>See rules →</button>
        </section>
      </div>
      <div className="draft-tray" id="tray" aria-label="weekly selection">
        {draft.candidates.map((candidate) => (
          <button
            key={candidate.song_key}
            draggable={!busy}
            className={dragging === candidate.song_key ? "dragging" : ""}
            aria-label={`Choose ${songName(candidate.song_key)}`}
            data-song={candidate.song_key}
            onDragStart={(e) => {
              e.dataTransfer.setData("text/plain", candidate.song_key);
              setDragging(candidate.song_key);
            }}
            onDragEnd={() => setDragging(null)}
            onClick={() => {
              setSelected(candidate.song_key);
              setPanel("pick");
            }}
          >
            <Art
              sharedLayout={false}
              song={candidate.song_key}
              title={songName(candidate.song_key)}
            />
          </button>
        ))}
      </div>
      {!draft.candidates.length && (
        <p>
          No appearances in this selection.{" "}
          <a href="/songs?view=rising">Open Rising now</a>
        </p>
      )}
      <button
        className="primary"
        disabled={busy}
        onClick={() => setPanel("close")}
      >
        Finish weekly picks
      </button>
      <a href="/songs?view=picks">Open picks →</a>
      {sheet && (
        <Sheet
          className="call-sheet"
          title={sheet === "rules" ? "Rules" : "Weekly picks"}
          close={() => setPanel(null)}
        >
          {sheet === "rules" ? (
            <>
              {!rules.some((r) => r.backed_by) && (
                <p>No rules yet. Back an example rule to start.</p>
              )}
              <p>Lists, in order: {ruleListOrder}</p>
              {rules.map((rule) => (
                <article className="draft-rule" key={rule.id} data-card>
                  <p>{rule.title}</p>
                  <span>{rule.backed_by ? "backed" : "example rule"}</span>
                  {rule.backed_by ? (
                    <button data-primary onClick={() => setPanel(null)}>
                      See the selection
                    </button>
                  ) : (
                    <button
                      data-primary
                      disabled={busy}
                      onClick={() => void act("back", rule.id)}
                    >
                      Back rule
                    </button>
                  )}
                </article>
              ))}
              <button onClick={() => setPanel(null)}>See the selection</button>
            </>
          ) : sheet === "close" ? (
            <>
              <h2>Finish weekly picks?</h2>
              <p>Rule picks are saved. No more Weekly picks this week.</p>
              <button
                className="primary"
                disabled={busy}
                onClick={() => void act("close")}
              >
                Close
              </button>
              <button disabled={busy} onClick={() => setPanel(null)}>
                Not now
              </button>
            </>
          ) : sheet === "pick" ? (
            <>
              <h2>Pick {selected ? songName(selected) : "this song"}?</h2>
              <p>It uses a pick this week.</p>
              <button
                className="primary"
                disabled={busy || !selected}
                onClick={() => selected && void pick(selected)}
              >
                Pick this song
              </button>
              <button onClick={() => setPanel(null)}>Not now</button>
            </>
          ) : (
            <>
              <p>
                {error === "draft_closed"
                  ? "This weekly selection is closed. Pick again next week."
                  : error === "draft_not_open"
                    ? "Waiting for the scheduled read. Retry after the read finishes."
                    : error === "rule_not_found"
                      ? "This rule is no longer available. Open the current rules."
                      : error === "call_limit"
                        ? `${CALLS_PER_WEEK} picks this week. The next week opens ${CALL_WEEK_DAY}.`
                        : error === "call_snapshot_expired"
                          ? "This card changed. Pick the refreshed card?"
                          : error === "call_checking"
                            ? "Checking this pick. Try again with the same card."
                            : retryAction === "close"
                              ? "The Weekly picks didn't close. Try again."
                              : "This request could not be checked. Try again."}
              </p>
              {error === "draft_closed" ? (
                <a className="primary" href="/songs?view=friday&board=1">
                  See results
                </a>
              ) : error === "draft_not_open" || error === "rule_not_found" ? (
                <a className="primary" href="/songs?view=friday&board=1">
                  Refresh Weekly picks
                </a>
              ) : error === "call_limit" ? (
                <a className="primary" href="/songs?view=picks">
                  Open your picks
                </a>
              ) : error === "call_snapshot_expired" ? (
                <a className="primary" href="/songs?view=friday&board=1">
                  Refresh card
                </a>
              ) : error === "call_checking" ? (
                <button
                  className="primary"
                  onClick={() => selected && void pick(selected)}
                >
                  Try again
                </button>
              ) : retryAction === "back" ? (
                <button
                  className="primary"
                  onClick={() => void act("back", ruleToBack)}
                >
                  Try again
                </button>
              ) : retryAction === "pick" ? (
                <button
                  className="primary"
                  onClick={() => selected && void pick(selected)}
                >
                  Try again
                </button>
              ) : (
                <button className="primary" onClick={() => void act("close")}>
                  Finish weekly picks
                </button>
              )}
            </>
          )}
        </Sheet>
      )}
    </section>
  );
}
