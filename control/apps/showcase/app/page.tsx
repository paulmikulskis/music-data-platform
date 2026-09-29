import { room, unavailable } from "../server/room";
import { movers, arrivals, citation } from "../server/reads";
import { tending } from "../server/platform";
import { Shell } from "../components/shell";
import { LocalDateLine } from "../components/local-time";
import { CoverStrip } from "../components/home/cover-strip";
import { LastNight } from "../components/home/last-night";
import { SourceStrip } from "../components/home/source-strip";
import { TeamPanel } from "../components/home/team-panel";
import { risingHeadline } from "../lib/music-facts";
import { unionKeys, observedKeys } from "../lib/source-keys";
import { cachedArtColor } from "../server/art";
export const dynamic = "force-dynamic";
export const metadata = { title: "Music Data Platform · Home" };
export default async function Home() {
  const current = await room();
  const result = await movers().catch(unavailable);
  const rows = result?.value.rows.slice(0, 4) ?? [];
  const landing =
    result && !rows.length ? await arrivals().catch(unavailable) : null;
  const arrival = landing?.value.rows[0];
  const songs = rows.length ? rows : arrival?.lead ? [arrival.lead] : [];
  const tended = await tending(
    current.person,
    songs.map((song) => song.song_key),
  );
  const source = rows.length ? result : landing;
  const provenance = source
    ? {
        ...citation(
          source.value,
          "Songs on the lists and charts. Open the proof for each reading.",
        ),
        sources: rows.length
          ? unionKeys(rows)
          : observedKeys(arrival?.observed),
      }
    : undefined;
  const headline = rows.length
    ? risingHeadline(rows)
    : {
        headline: arrival?.lead
          ? `${arrival.songs} songs new on lists and charts.`
          : "",
        detail: null,
      };
  const colors = Object.fromEntries(
    songs
      .map((song) => [song.song_key, cachedArtColor(song.song_key)])
      .filter((pair): pair is [string, string] => typeof pair[1] === "string"),
  );
  return (
    <Shell
      handle={current.handle}
      csrf={current.csrf_token}
      songs={songs}
      provenances={[provenance]}
      cached={source?.state}
      tending={tended}
      build={source?.value.build}
    >
      <section className="home new-home">
        <div className="home-heading">
          <p className="eyebrow">
            <LocalDateLine weekdayOnlyOnPhone />
          </p>
          {headline.headline && (
            <h1>
              <a href="/songs?view=rising">{headline.headline}</a>
            </h1>
          )}
          {headline.detail && (
            <p className="headline-detail">{headline.detail}</p>
          )}
        </div>
        <CoverStrip
          movers={rows}
          arrival={arrival}
          provenance={provenance}
          unavailable={!result || (!rows.length && !landing)}
          colors={colors}
        />
        <div className="home-below">
          <LastNight />
          <SourceStrip />
        </div>
        <TeamPanel />
      </section>
    </Shell>
  );
}
