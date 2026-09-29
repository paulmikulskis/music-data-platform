import { CallsProvider } from "../../../../components/call-it";
import { callOffers, songProjection } from "../../../../server/call-offers";
import { capturedProof } from "../../../../server/proof-store";
import { notFound, redirect } from "next/navigation";
import { routeKey } from "../../../../lib/route-key";
import { room, unavailable } from "../../../../server/room";
import {
  alias,
  songMovement,
  songHistory,
  identities,
  citation,
  movementReadiness,
  songPlaces,
  songGroup,
  historyWindow,
  playlistDays,
} from "../../../../server/reads";
import { groupSongDaySql } from "../../../../server/citation-sql";
import { rememberHistory } from "../../../../server/proof-store";
import { groupDays } from "../../../../lib/cluster";
import { sourceKeys } from "../../../../lib/source-keys";
import { tending } from "../../../../server/platform";
import { cityPlace, marketPlace } from "../../../../lib/places";
import { Shell } from "../../../../components/shell";
import { Song } from "../../../../components/song";
import { Empty } from "../../../../components/movers";
import { verifyProof } from "../../../../server/proof-token";
export const dynamic = "force-dynamic";
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ key: string }>;
  searchParams: Promise<{ ranking?: string; showcase_proof?: string }>;
}) {
  const key = routeKey((await params).key);
  if (!key) notFound();
  const current = await room();
  const query = await searchParams;
  const proof = verifyProof(query.showcase_proof ?? null, current.handle);
  if (proof?.level === "unavailable" && proof.song === key)
    return (
      <Shell handle={current.handle} csrf={current.csrf_token}>
        <Empty
          title="Proof is unavailable."
          detail="The saved source records could not be found."
          href={`/s/song/${encodeURIComponent(key)}`}
          action="Open the song"
        />
      </Shell>
    );
  const resolved = await alias(key).catch(unavailable);
  const canonical = resolved?.value.rows[0]?.song_key;
  if (canonical && canonical !== key)
    redirect(`/s/song/${encodeURIComponent(canonical)}`);
  const [ranking, own, ownCopies, ready, ownPlaces, tended, group] =
    await Promise.all([
      songMovement(key).catch(unavailable),
      songHistory(key).catch(unavailable),
      identities(key).catch(unavailable),
      movementReadiness().catch(unavailable),
      songPlaces(key).catch(unavailable),
      tending(current.person, [key]),
      songGroup(key).catch(unavailable),
    ]);
  // A matched song reads as one, like its card: its copies' days add up and every copy shows.
  // If any copy's read fails or comes from another build, the page keeps this key alone.
  // A group larger than one page would show part of itself, so it keeps the key alone.
  const members = group?.value.next_cursor
    ? []
    : (group?.value.rows ?? [])
        .map((row) => row.song_key)
        .filter((member) => member !== key);
  const matched =
    own && members.length
      ? await Promise.all([
          Promise.all(members.map((member) => songHistory(member))),
          identities([key, ...members]),
          songPlaces([key, ...members]),
        ]).catch(unavailable)
      : null;
  const grouped =
    own &&
    matched &&
    matched[0].every(
      ({ value: { build } }) =>
        build.cycle_id === own.value.build.cycle_id &&
        build.close_no === own.value.build.close_no &&
        build.built_at === own.value.build.built_at,
    )
      ? matched
      : null;
  const history =
    own && grouped
      ? {
          ...own,
          value: {
            ...own.value,
            rows: groupDays(key, [
              own.value.rows,
              ...grouped[0].map((read) => read.value.rows),
            ]),
            sql: groupSongDaySql([key, ...members], historyWindow()),
          },
        }
      : own;
  if (history && grouped)
    rememberHistory(key, history.value.build, history.value.rows);
  const listRead = await playlistDays(
    grouped ? [key, ...members] : [key],
    historyWindow(),
  ).catch(unavailable);
  const listDays =
    listRead &&
    history &&
    listRead.value.build.cycle_id === history.value.build.cycle_id &&
    listRead.value.build.close_no === history.value.build.close_no
      ? listRead
      : null;
  const playlistProvenance = listDays
    ? citation(
        listDays.value,
        "Distinct playlists with new appearances that day, across matched copies. Chart lists are counted in Places.",
        listDays.value.sql,
      )
    : undefined;
  const displayedDays = (history?.value.rows ?? []).map((row) => ({
    ...row,
    editorial_adds: listDays
      ? (listDays.value.rows.find((entry) => entry.day === row.day)
          ?.playlists ?? (row.editorial_adds === null ? null : 0))
      : null,
  }));
  const copies = grouped ? grouped[1] : ownCopies;
  const reached = grouped ? grouped[2] : ownPlaces;
  const methods = sourceKeys(
    group?.value.rows.find((row) => row.song_key === key)?.cluster_methods,
  );
  // Cities in the order the song reached them; a country chart lights its market.
  const places = (reached?.value.rows ?? [])
    .map((row) =>
      row.city
        ? cityPlace(row.city)
        : row.country
          ? marketPlace(row.country)
          : null,
    )
    .filter((place) => place !== null);
  const latest = history?.value.rows.at(-1);
  const song =
    capturedProof(query.ranking ?? "", key) ??
    ranking?.value.rows[0] ??
    (latest
      ? {
          ...latest,
          rank: "0",
          score_parts: [],
          playlist_count: null,
          reason_rule: "No ranked movement in this window.",
          ranking_build: "",
          coverage: [],
          evidence: [],
          momentum_score: 0,
          window_days: null,
          movement_list: "unplaced",
          age_basis: null,
        }
      : undefined);
  const p = history
    ? citation(
        history.value,
        grouped
          ? "Daily observations from tracked playlists, city charts and listening counts, added up across matched copies. Cities are the most any one copy reached. Missing days are gaps."
          : "Daily observations from tracked playlists, city charts and listening counts. Missing days are gaps.",
        history.value.sql,
        history.value.rows.at(-1)?.day,
      )
    : undefined;
  const offers = await callOffers(
    current,
    latest && history
      ? history.value.rows.map((day) =>
          songProjection(
            day,
            reached?.value.rows ?? null,
            [history.value.build, ...(reached ? [reached.value.build] : [])],
            latest.day,
          ),
        )
      : [],
  ).catch(() => ({}));
  return (
    <CallsProvider offers={offers}>
      <Shell
        handle={current.handle}
        songs={song ? [song] : []}
        csrf={current.csrf_token}
        provenances={[
          p,
          playlistProvenance,
          copies
            ? citation(
                copies.value,
                "Copies linked to this recording.",
                copies.value.sql,
              )
            : undefined,
        ]}
        cached={history?.state}
        tending={tended}
      >
        {song && history && p ? (
          <Song
            song={song}
            days={displayedDays}
            copies={copies?.value.rows ?? []}
            provenance={p}
            readiness={ready?.value.rows}
            playlistProvenance={playlistProvenance}
            places={places}
            shownPlaces={reached ? reached.value.rows : null}
            placesBuild={reached?.value.build}
            callKey={key}
            match={
              grouped ? { copies: members.length + 1, methods } : undefined
            }
          />
        ) : !resolved || !history || !ranking ? (
          <Empty
            title="the song is out of reach."
            detail="Can't reach the platform."
            href={`/s/song/${encodeURIComponent(key)}`}
            action="Retry"
          />
        ) : (
          <Empty
            title="song not found."
            detail="No song with this key."
            href="/search"
            action="Search the Library"
          />
        )}
      </Shell>
    </CallsProvider>
  );
}
