import { room, unavailable } from "../../server/room";
import { events, sources } from "../../server/platform";
import { collectionFailure } from "../../server/collection-status";
import { liveList } from "../../lib/rights";
import { Shell } from "../../components/shell";
import { LocalTime } from "../../components/local-time";
export const dynamic = "force-dynamic";
export default async function Page() {
  const current = await room();
  const [activity, read, failure] = await Promise.all([
    events(current.person).catch(unavailable),
    sources(current.person).catch(unavailable),
    collectionFailure(current.person).catch(unavailable),
  ]);
  const active = liveList(read?.value.sources);
  const lastRead = active
    .map((source) => source.last_read)
    .filter((time) => time !== null)
    .sort()
    .at(-1);
  return (
    <Shell handle={current.handle} csrf={current.csrf_token}>
      <section className="live-room">
        <p className="eyebrow">Live</p>
        <h1>collection status.</h1>
        <p>
          {failure?.failedAt
            ? "Collection needs attention."
            : !failure
              ? "Collection status is unavailable. Refresh to check again."
              : activity?.runner.state === "busy"
                ? "Collection is running."
                : activity?.runner.state === "idle"
                  ? "Waiting for the next read."
                  : "Collection status is unavailable. Refresh to check again."}
        </p>
        {failure?.failedAt && (
          <>
            <p>
              Reported · <LocalTime at={failure.failedAt} relativeDay />
            </p>
            <p>
              {activity?.runner.next_scheduled_at ? (
                <>
                  Next collection from{" "}
                  <LocalTime
                    at={activity.runner.next_scheduled_at}
                    relativeDay
                  />
                  . Start time may vary.
                </>
              ) : (
                "Next collection time is unavailable. Refresh to check again."
              )}
            </p>
          </>
        )}
        {lastRead && !failure?.failedAt && (
          <p>
            Latest collection · <LocalTime at={lastRead} />
          </p>
        )}
        <a className="primary" href="/sources?view=sources">
          Open sources →
        </a>
      </section>
    </Shell>
  );
}
