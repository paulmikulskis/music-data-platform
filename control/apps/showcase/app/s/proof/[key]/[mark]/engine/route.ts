import { session } from "../../../../../../server/session";
import {
  capturedProof,
  capturedHistory,
} from "../../../../../../server/proof-store";
import { movers } from "../../../../../../server/reads";
import { resolveProof, type ProofResult } from "../../../../../../server/proof";
import { signProof } from "../../../../../../server/proof-token";
import { viewerPath } from "../../../../../../lib/proof-link";
import { routeKey } from "../../../../../../lib/route-key";
import { origin } from "@mdp/showcase-auth";
// The operator console behind a proof summary: the run that landed the fact's entries.
// Its Back bar returns to the summary the viewer came from.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ key: string; mark: string }> },
) {
  const current = await session();
  if (!current) return Response.redirect(new URL("/sign-in", origin()), 303);
  const { key: raw, mark } = await params;
  const key = routeKey(raw);
  if (!key) return new Response("Song not found. Open /library.", { status: 404 });
  const song = `/s/song/${encodeURIComponent(key)}`;
  const query = new URL(request.url).searchParams;
  const back = viewerPath(query.get("back")) ?? song;
  const ranking = query.get("ranking") ?? "";
  await movers(50).catch(() => null);
  const captured = capturedProof(ranking, key);
  const day = query.get("day") ?? "";
  const history = capturedHistory(key, query.get("history") ?? "", day);
  const entry = query.has("history")
    ? history && /^[0-2]$/.test(mark)
      ? {
          component: [
            "playlist_adds",
            "shazam_spread_gain",
            "stream_rate_gain",
          ][Number(mark)],
          relation: "mart_song_day",
          row_key: { song_key: key, day },
          window: { day },
          input_build: history,
        }
      : undefined
    : captured?.evidence[Number(mark)];
  const proof: ProofResult =
    entry && /^\d+$/.test(mark)
      ? await resolveProof(current.person, entry).catch((): ProofResult => ({
          level: "unavailable",
          href: song,
        }))
      : { level: "unavailable", href: song };
  // An unavailable record returns to the summary, which says so.
  const target = new URL(
    proof.level === "unavailable" ? back : proof.href,
    origin(),
  );
  target.searchParams.set(
    "showcase_proof",
    signProof({
      level: proof.level,
      song: key,
      handle: current.handle,
      unstamped: !!entry && !entry.input_build.built_at,
      back,
    }),
  );
  return Response.redirect(target, 303);
}
