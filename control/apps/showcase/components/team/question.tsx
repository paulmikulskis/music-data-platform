"use client";
import type { LinkPreview } from "../../lib/links";
import { LinkOut } from "../link-out";
import { useEffect, useState } from "react";
import { z } from "zod";
import { teamResult } from "../../lib/team-questions";
import { placeName } from "../../lib/places";
import { dayLabel } from "../../lib/presentation";
import { Art } from "../movers";
import { CopySQL } from "../copy-sql";
import { LocalTime } from "../local-time";
const response = teamResult.extend({ workbench: z.string().nullable() });
type Read =
  | { state: "reading" }
  | { state: "ready"; value: z.infer<typeof response> }
  | { state: "failed" };
// The one starter question. It runs on open, shows up to five results with covers, and offers
// the SQL for later. Open in Workbench carries the same SQL to the Console's Workbench, which
// shows it before any session runs it; without that link the plain Workbench opens.
export function TeamQuestion({
  id,
  question,
  sql,
  workbench,
  code,
}: {
  id: string;
  question: string;
  sql: string;
  workbench: LinkPreview | null;
  code: LinkPreview | null;
}) {
  const [read, setRead] = useState<Read>({ state: "reading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetch(`/s/team-question?${new URLSearchParams({ id })}`, {
      signal: controller.signal,
    })
      .then(async (result) => {
        if (!result.ok) throw new Error("Question unavailable");
        setRead({
          state: "ready",
          value: response.parse(await result.json()),
        });
      })
      .catch(() => {
        if (!controller.signal.aborted) setRead({ state: "failed" });
      });
    return () => controller.abort();
  }, [id, attempt]);
  return (
    <section id="try" className="team-question" aria-label="Try a question">
      <h2>Try a question</h2>
      <p>{question}</p>
      {read.state === "reading" ? (
        <p role="status">Reading.</p>
      ) : read.state === "failed" ? (
        <p role="status">
          Taking too long.{" "}
          <button onClick={() => setAttempt((n) => n + 1)}>Retry</button>, or
          copy the SQL to try later.
        </p>
      ) : read.value.rows.length === 0 ? (
        <p role="status">
          No city picked up a song this week. Copy the SQL to try it later.
        </p>
      ) : (
        <ol className="question-results">
          {read.value.rows.map((row, i) => (
            <li key={`${row.song_key ?? row.title}-${row.city}-${i}`}>
              {row.song_key ? (
                <Art
                  song={row.song_key}
                  title={row.title}
                  sharedLayout={false}
                />
              ) : (
                <div className="art" data-art="missing" />
              )}
              <span className="result-title">{row.title}</span>
              <span className="result-line">{row.artist}</span>
              <span className="result-line">
                {placeName({ city: row.city, country: row.country })} · first
                seen {dayLabel(row.first_day)}
              </span>
            </li>
          ))}
        </ol>
      )}
      {read.state === "ready" && (
        <p className="result-line">
          Up to 5 results · read <LocalTime at={read.value.queried_at} /> ·
          first seen means the first day in the platform&apos;s readings.
        </p>
      )}
      <div className="question-actions">
        <CopySQL sql={sql} />
        <LinkOut
          preview={workbench}
          signedHref={read.state === "ready" ? read.value.workbench : null}
        />
        <LinkOut preview={code} />
      </div>
      <details className="question-sql">
        <summary>Query for later</summary>
        <textarea aria-label="Query for later" readOnly value={sql} rows={12} />
        <p>Select and copy. It runs as the analyst login in the Workbench.</p>
      </details>
    </section>
  );
}
