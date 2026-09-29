"use client";
import { LocalTime } from "./local-time";
import { cycleProofLink, sourceProofLink } from "../lib/proof-link";
import { CALL_UNDO_MS } from "../lib/calls";
import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import type { SavedCall, CallObservation } from "../lib/calls";
import type { CallRead } from "../server/call-reads";
import type { Provenance } from "./number";
import { Number } from "./number";
import { Art } from "./movers";
import { Sheet } from "./sheet";
import { CityMap } from "./city-map";
import { cityPlace, marketPlace, placeName } from "../lib/places";
import { shortLabel } from "../lib/presentation";
import { callMutation } from "./call-it";
import { useRouter } from "next/navigation";
const name = placeName;
export function CallCard({
  proofs,
  call,
  row,
  provenance,
  csrf,
  mine,
  lastRead,
  stale,
}: {
  proofs: string[];
  call: SavedCall;
  row: CallRead["rows"][number] | null;
  provenance?: Provenance;
  csrf: string;
  mine: boolean;
  lastRead: string | null;
  stale: boolean;
}) {
  const router = useRouter();
  const reduced = useReducedMotion();
  const [front, setFront] = useState(true);
  // The flip remounts the card; focus returns to the flip control on the new face.
  const flipControl = useRef<HTMLButtonElement>(null);
  const flipped = useRef(false);
  useEffect(() => {
    if (flipped.current) flipControl.current?.focus();
  }, [front]);
  const [sheet, setPanel] = useState<string | null>(null);
  const [place, setPlace] = useState<CallObservation | null>(null);
  const [shownPage, setShownPage] = useState(0);
  const [placePage, setPlacePage] = useState(0);
  const [failure, setFailure] = useState(false);
  const shown = call.facts.places_shown;
  const song = `/s/song/${encodeURIComponent(call.song_key)}`;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const undo = now - Date.parse(call.submitted_at) <= CALL_UNDO_MS;
  const old = now - Date.parse(call.submitted_at) > 28 * 86400000;
  const proofHref = (index: number) =>
    `/songs/picks/${call.id}/proof?token=${encodeURIComponent(proofs[index] ?? "")}`;
  const frozen: Provenance = {
    queried_at: call.submitted_at,
    scope: "global",
    inputs: call.facts.builds,
    sources: call.facts.source_keys,
    observed_at: call.facts.facts_day,
    query: "Places shown on this card when the pick was saved.",
    sql: "SELECT facts FROM control.showcase_call WHERE id=$call",
  };
  const places = [...(shown ?? []), ...(row?.places ?? [])];
  const map = places
    .map((p) =>
      p.city ? cityPlace(p.city) : p.country ? marketPlace(p.country) : null,
    )
    .filter((p) => p !== null);
  async function change() {
    const answer = await callMutation(csrf, {
      action: undo ? "undo" : "hide",
      id: call.id,
    }).catch(() => null);
    if (answer?.error === "showcase_link_expired")
      router.push("/sign-in?reason=ended");
    else if (answer?.call) {
      router.push("/songs?view=picks");
      router.refresh();
    } else setFailure(true);
  }
  return (
    <section className="call-room">
      <p className="eyebrow">Pick</p>
      <h1>since the pick.</h1>
      {(stale ||
        (row && (!row.matched || row.changed || BigInt(row.copies) > 0n)) ||
        old) && (
        <div className="record-notes">
          {stale && (
            <p>
              Updated{" "}
              {provenance?.queried_at ? (
                <LocalTime at={provenance.queried_at} relativeDay />
              ) : (
                "earlier"
              )}{" "}
              · <a href={`/songs/picks/${call.id}`}>Retry</a>
            </p>
          )}
          {row && !row.matched && (
            <button onClick={() => setPanel("match")}>
              Apple Music match pending →
            </button>
          )}
          {row?.changed && (
            <p>
              Matched differently since the pick. <a href={song}>See copies</a>
            </p>
          )}
          {row && !row.changed && BigInt(row.copies) > 0n && (
            <p>
              Places include matched copies. <a href={song}>See copies</a>
            </p>
          )}
          {old && (
            <p>
              28 days since the pick. Open the song for recent observations.{" "}
              <a href={song}>Open song</a>
            </p>
          )}
        </div>
      )}
      <motion.article
        data-card
        className="record-card"
        animate={{ rotateY: 0, opacity: 1 }}
        initial={{ rotateY: reduced ? 0 : 8, opacity: 0 }}
        transition={{ duration: 0.22 }}
        key={String(front)}
      >
        <a className="record-song" href={song}>
          <Art song={call.song_key} title={row?.title ?? "song"} />
          <div>
            <h2>{shortLabel(row?.title ?? "Open song", 4)}</h2>
            {row?.artist && <p>{shortLabel(row.artist, 3)}</p>}
          </div>
        </a>
        <button
          ref={flipControl}
          className="call-flip"
          onClick={() => {
            flipped.current = true;
            setFront(!front);
          }}
          aria-label="Flip pick card"
        >
          ↶
        </button>
        <p>{front ? "on the card" : "seen since, not on the card"}</p>
        {front ? (
          shown === null ? (
            <p>Places weren’t shown.</p>
          ) : (
            <Number
              compact
              value={String(shown.length)}
              label={shown.length === 1 ? "place" : "places"}
              provenance={frozen}
            />
          )
        ) : row ? (
          <Number
            compact
            value={row.total}
            label={row.total === "1" ? "place" : "places"}
            provenance={provenance}
          />
        ) : (
          <p>
            Places load when the platform answers.{" "}
            <a href={`/songs/picks/${call.id}`}>Retry</a>
          </p>
        )}
        <button
          className="primary"
          data-primary
          onClick={() => setPanel("places")}
        >
          See places
        </button>
        {proofs.length ? (
          <a href={proofHref(0)}>proof →</a>
        ) : (
          <button onClick={() => setPanel("proof")}>proof →</button>
        )}
      </motion.article>
      <div className="record-notes">
        {mine && !call.undone_at && !call.hidden_at && (
          <button onClick={() => setPanel("menu")}>
            {undo ? "Undo" : "Hide"}
          </button>
        )}
        <button onClick={() => setPanel("share")}>Share</button>
        <a href="/songs?view=picks">Open picks →</a>
      </div>
      {sheet === "match" && (
        <Sheet
          className="call-sheet"
          title="Apple Music match"
          close={() => setPanel(null)}
        >
          <p>
            Shazam places show up here once this song is matched to Apple Music.
          </p>
          <a className="primary" href={song}>
            Open song
          </a>
        </Sheet>
      )}
      {sheet === "share" && (
        <Sheet
          className="call-sheet"
          title="Sharing"
          close={() => setPanel(null)}
        >
          <p>Picks stay inside Music Data Platform.</p>
          <a href="/sources">Open Sources</a>
        </Sheet>
      )}
      {sheet === "proof" && (
        <Sheet
          className="call-sheet"
          title="Proof"
          close={() => setPanel(null)}
        >
          <p>
            Proof shows the song&apos;s appearances as read today, not as they
            were when the pick was saved.
          </p>
          <a
            className="primary"
            href={cycleProofLink(call.facts.builds[0], "/songs?view=rising", {
              song: call.song_key,
            })}
          >
            See proof
          </a>
        </Sheet>
      )}
      {sheet === "places" && (
        <Sheet
          className="call-sheet"
          title="Places"
          close={() => setPanel(null)}
        >
          {call.facts.card === "mover" && call.facts.shown.length > 0 && (
            <div className="card-facts">
              {call.facts.shown.map((fact) => (
                <p key={fact.component}>{fact.text}</p>
              ))}
            </div>
          )}
          {shown === null ? (
            <p>Places weren&apos;t on this card.</p>
          ) : (
            <div className="place-names">
              {shown.slice(shownPage * 6, shownPage * 6 + 6).map((p, i) => (
                <span key={i}>{name(p)}</span>
              ))}
            </div>
          )}
          {shown && shown.length > 6 && (
            <button
              onClick={() =>
                setShownPage(
                  (shownPage + 1) * 6 >= shown.length ? 0 : shownPage + 1,
                )
              }
            >
              More places on the card
            </button>
          )}
          <p className="call-line">Picked</p>
          <div className="place-names">
            {row?.places.slice(placePage * 6, placePage * 6 + 6).map((p, i) => (
              <button
                key={i}
                onClick={() => {
                  setPlace(p);
                  setPanel("place");
                }}
              >
                {name(p)}
              </button>
            ))}
          </div>
          {row && BigInt(row.total) > 60n && (
            <Number
              compact
              value={`60 of ${row.total}`}
              label="shown"
              provenance={provenance}
            />
          )}
          {row?.total === "0" && (
            <p>
              Nothing new yet.{" "}
              {lastRead ? (
                <>
                  Shazam charts last read{" "}
                  <LocalTime at={lastRead} relativeDay />.
                </>
              ) : (
                "Read time not measured yet."
              )}{" "}
              <a
                href={cycleProofLink(
                  provenance?.inputs?.find(
                    (b) => b.relation === "marts.mart_shazam_chart_daily",
                  ),
                  sourceProofLink("sz_chart"),
                  { song: call.song_key },
                )}
              >
                See the charts
              </a>
            </p>
          )}
          {!row && (
            <p>
              Places load when the platform answers.{" "}
              <a href={`/songs/picks/${call.id}`}>Retry</a>
            </p>
          )}
          {placePage > 0 && (
            <button onClick={() => setPlacePage(placePage - 1)}>
              Previous places
            </button>
          )}
          {row && row.places.length > (placePage + 1) * 6 && (
            <button onClick={() => setPlacePage(placePage + 1)}>
              More places
            </button>
          )}
          <button className="primary" onClick={() => setPanel("map")}>
            Open map →
          </button>
        </Sheet>
      )}
      {sheet === "map" && (
        <Sheet
          className="call-sheet"
          title="Places map"
          close={() => setPanel(null)}
        >
          <CityMap places={map} />
          <button className="primary" onClick={() => setPanel("places")}>
            See places
          </button>
        </Sheet>
      )}
      {sheet === "place" && place && (
        <Sheet
          className="call-sheet"
          title={name(place)}
          close={() => setPanel(null)}
        >
          <p>
            {new Date(`${place.chart_date}T00:00:00Z`).toLocaleDateString(
              "en-GB",
              { day: "numeric", month: "short", timeZone: "UTC" },
            )}{" "}
            · Shazam {place.chart_type ?? "chart"}
            {place.matched_copy ? " · matched copy" : ""}
          </p>
          <Number
            compact
            value={String(place.position)}
            label="chart position"
            provenance={provenance}
          />
          <a
            className="primary"
            href={proofHref(
              row?.places.findIndex(
                (p) => p.country === place.country && p.city === place.city,
              ) ?? -1,
            )}
          >
            See the appearances
          </a>
        </Sheet>
      )}
      {sheet === "menu" && (
        <Sheet className="call-sheet" title="Pick" close={() => setPanel(null)}>
          <h2>{undo ? "Undo this pick?" : "Hide this pick?"}</h2>
          <p>
            {undo ? "It still uses a weekly slot." : "It stays on the record."}
          </p>
          {failure && (
            <p>
              Update needs another try.{" "}
              <a href={`/songs/picks/${call.id}`}>Refresh pick</a>
            </p>
          )}
          <button className="primary" onClick={change}>
            {undo ? "Undo" : "Hide"}
          </button>
        </Sheet>
      )}
    </section>
  );
}
