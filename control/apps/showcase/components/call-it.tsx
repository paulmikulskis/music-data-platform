"use client";
import { LocalTime } from "./local-time";
import { CALL_UNDO_MS, CALLS_PER_WEEK, CALL_WEEK_DAY } from "../lib/calls";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, useReducedMotion } from "motion/react";
import { z } from "zod";
import { savedCall, type CallOffer, type SavedCall } from "../lib/calls";
import { Number } from "./number";
import { Sheet } from "./sheet";
const Calls = createContext<Record<string, CallOffer>>({});
// Calls left this week, shared by every card on the page. A saved call updates it from the server.
const Left = createContext<{
  left: number | null;
  setLeft: (left: number) => void;
}>({ left: null, setLeft: () => {} });
export function CallsProvider({
  offers,
  children,
}: {
  offers: Record<string, CallOffer>;
  children: React.ReactNode;
}) {
  const served = Object.values(offers)[0]?.left ?? null;
  const [left, setLeft] = useState({ served, value: served });
  // A fresh server render carries the current count; it replaces the local one.
  if (left.served !== served) setLeft({ served, value: served });
  return (
    <Calls.Provider value={offers}>
      <Left.Provider
        value={{
          left: left.value,
          setLeft: (value) => setLeft({ served, value }),
        }}
      >
        {children}
      </Left.Provider>
    </Calls.Provider>
  );
}
export const useCallOffer = (song: string) => useContext(Calls)[song];
const result = z.object({
  call: savedCall.optional(),
  left: z.number().int().optional(),
  error: z.string().optional(),
});
export async function callMutation(csrf: string, data: unknown) {
  const response = await fetch("/calls", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-csrf-token": csrf },
    body: JSON.stringify(data),
    signal: AbortSignal.timeout(8000),
  });
  // A lapsed session gets its own code; the caller opens the sign-in page.
  if (response.status === 401) return { error: "showcase_link_expired" };
  return result.parse(await response.json());
}
export function CallIt({
  song,
  offerKey,
  primary = true,
  onRefresh,
}: {
  song: string;
  offerKey?: string;
  primary?: boolean;
  onRefresh?: () => void;
}) {
  const offer = useCallOffer(offerKey ?? song);
  return offer ? (
    <CallButton offer={offer} primary={primary} onRefresh={onRefresh} />
  ) : null;
}
function CallButton({
  offer,
  primary,
  onRefresh,
}: {
  offer: CallOffer;
  primary: boolean;
  onRefresh?: () => void;
}) {
  const router = useRouter();
  const refreshOffer = onRefresh ?? (() => router.refresh());
  const reduced = useReducedMotion();
  const shared = useContext(Left);
  const left = shared.left ?? offer.left;
  const [call, setCall] = useState<SavedCall | null>(offer.existing);
  const [sheet, setPanel] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [undo, setUndo] = useState(
    !!call && Date.now() - Date.parse(call.submitted_at) <= CALL_UNDO_MS,
  );
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [refreshKey, setRefreshKey] = useState<string | null>(null);
  if (refreshKey && refreshKey !== offer.signed.idempotency_key) {
    setRefreshKey(null);
    setPanel("refreshed");
  }
  const alive = useRef(true);
  const sending = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (retry.current) clearTimeout(retry.current);
    };
  }, []);
  useEffect(() => {
    if (!call) return;
    const remaining =
      CALL_UNDO_MS - (Date.now() - Date.parse(call.submitted_at));
    const timeout = setTimeout(() => setUndo(false), Math.max(0, remaining));
    return () => clearTimeout(timeout);
  }, [call]);
  async function submit() {
    if (sending.current) return;
    sending.current = true;
    setPanel(null);
    setChecking(true);
    try {
      const answer = await callMutation(offer.csrf, {
        action: "call",
        signed: offer.signed,
      });
      if (!alive.current) return;
      if (answer.error === "showcase_link_expired") {
        router.push("/sign-in?reason=ended");
        return;
      }
      if (answer.error === "call_checking") throw new Error("retry");
      if (answer.call) {
        if (answer.left !== undefined) shared.setLeft(answer.left);
        setCall(answer.call);
        setUndo(
          Date.now() - Date.parse(answer.call.submitted_at) <= CALL_UNDO_MS,
        );
        setChecking(false);
      } else {
        setChecking(false);
        setPanel(answer.error ?? "call_invalid");
        if (answer.error === "call_snapshot_expired") {
          setRefreshKey(offer.signed.idempotency_key);
          refreshOffer();
        }
      }
    } catch {
      if (alive.current)
        retry.current = setTimeout(() => {
          sending.current = false;
          void submit();
        }, 1500);
    } finally {
      sending.current = false;
    }
  }
  async function withdraw() {
    if (!call) return;
    const answer = await callMutation(offer.csrf, {
      action: "undo",
      id: call.id,
    }).catch(() => null);
    if (answer?.error === "showcase_link_expired") {
      router.push("/sign-in?reason=ended");
      return;
    }
    if (answer?.call) {
      setCall(null);
      setPanel(null);
      router.refresh();
    } else {
      setUndo(false);
      setPanel(answer?.error ?? "call_invalid");
    }
  }
  if (call || checking)
    return (
      <>
        <motion.div
          className="call-stamp"
          initial={{ opacity: 0, y: reduced ? 0 : -4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          {checking || !call ? (
            <span role="status">
              checking · <a href="/songs?view=picks">Open picks</a>
            </span>
          ) : (
            <>
              <a
                data-primary={primary || undefined}
                href={`/songs/picks/${call.id}`}
              >
                picked
                <span className="call-clock">
                  {" "}
                  · <LocalTime at={call.submitted_at} relativeDay />
                </span>
              </a>
              {undo && <button onClick={() => setPanel("undo")}>Undo</button>}
            </>
          )}
        </motion.div>
        {sheet && (
          <Sheet
            className="call-sheet"
            title="Pick"
            close={() => setPanel(null)}
          >
            <h2>
              {sheet === "undo" ? "Undo this pick?" : "Undo time passed."}
            </h2>
            {sheet === "undo" ? (
              <button className="primary" onClick={withdraw}>
                Undo
              </button>
            ) : (
              <a
                className="primary"
                href={call ? `/songs/picks/${call.id}` : "/songs?view=picks"}
              >
                Open pick to hide it
              </a>
            )}
          </Sheet>
        )}
      </>
    );
  return (
    <>
      <button
        className="card-verb call-button"
        data-primary={primary || undefined}
        onClick={() => {
          void submit();
        }}
      >
        {offer.otherInitial && (
          <span className="other-initial">{offer.otherInitial}</span>
        )}
        {offer.other ? "Pick this song too" : "Pick this song"}
        <svg className="hold-ring" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="10" />
        </svg>
      </button>
      {sheet && (
        <Sheet className="call-sheet" title="Pick" close={() => setPanel(null)}>
          {sheet === "confirm" || sheet === "refreshed" ? (
            <>
              <h2>
                {sheet === "refreshed"
                  ? "Pick the refreshed card?"
                  : "Pick this song?"}
              </h2>
              <p>
                {sheet === "refreshed"
                  ? "This card changed. It goes on the record as it shows now."
                  : "It goes on the record as the card shows it now."}
              </p>
              <Number
                compact
                value={String(left)}
                label="picks left this week"
                provenance={{
                  queried_at: new Date().toISOString(),
                  scope: "global",
                  provenance: "live query",
                  query: `Accepted picks this ${CALL_WEEK_DAY} week, including undone picks.`,
                  sql: "SELECT count(*) FROM control.showcase_call WHERE author = $author AND week_start = $week",
                }}
              />
              <button className="primary" onClick={submit}>
                Pick this song
              </button>
              <button onClick={() => setPanel(null)}>Not now</button>
            </>
          ) : sheet === "call_limit" ? (
            <>
              <h2>Weekly picks used.</h2>
              <p>
                {CALLS_PER_WEEK} picks this week. The next week opens{" "}
                {CALL_WEEK_DAY}.
              </p>
              <a className="primary" href="/songs?view=picks">
                Open your picks
              </a>
            </>
          ) : sheet === "call_snapshot_expired" ? (
            <>
              <h2>Card needs a refresh.</h2>
              <p>This card changed. Pick the refreshed card?</p>
              <button
                className="primary"
                onClick={() => {
                  setRefreshKey(offer.signed.idempotency_key);
                  refreshOffer();
                }}
              >
                Refresh card
              </button>
            </>
          ) : (
            <>
              <h2>Pick needs another try.</h2>
              <p>This request could not be checked.</p>
              <button className="primary" onClick={submit}>
                Try again
              </button>
            </>
          )}
        </Sheet>
      )}
    </>
  );
}
