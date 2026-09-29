"use client";
import { honesty } from "../lib/honesty";
import { useRef, useState } from "react";
import type { Holdings as HoldingsData } from "../server/platform";
import type { Provenance } from "./number";
import type { Rights } from "../server/models";
import { Metric } from "./metric";
import { Sheet } from "./sheet";
import { Hover } from "./hover";
import { Tending } from "./tending";
import { useTending } from "./sources";
import {
  barHeights,
  collectionBegan,
  counted,
  peakDay,
  weekDays,
  type WeekDay,
} from "../lib/collection";
import { dayName, weekdayName } from "../lib/presentation";
import {
  permissionLine,
  rightsGroups,
  type RightsEntry,
  type RightsState,
} from "../lib/rights";
// holds and rights are the two tabs; collection and sources open their sheets on arrival.
export type HoldingsView = "holds" | "collection" | "rights" | "sources";
type Group = RightsState | "own" | "all";
const groupNames: Record<Group, string> = {
  collecting: "Collecting now",
  quiet: "Not read yet",
  off: "Switched off",
  waiting: "Not connected yet",
  own: "the platform's own lists",
  all: "All sources",
};
const groupLines: Record<Group, string> = {
  collecting: "Switched on and read within its schedule.",
  quiet: "Switched on. Open a source to check its latest attempt.",
  off: "Built and switched off. Open a source for its collection dates.",
  waiting: "On record, not set up or waiting for provider access.",
  own: "Written by the operator, not collected.",
  all: "Which sources are read now is not measured yet.",
};
// The sheet's tabs, in the order a viewer reads them.
const measuredGroups = [
  "collecting",
  "quiet",
  "off",
  "waiting",
  "own",
] as const;
const page = 12;
const whole = (value: string | bigint) => BigInt(value).toLocaleString("en-US");
// A bar per day, Monday to Sunday. Rounded data end, anchored to the baseline.
function barPath(x: number, width: number, height: number) {
  const r = Math.min(4, height);
  const top = 100 - height;
  return `M${x},100V${top + r}Q${x},${top} ${x + r},${top}H${x + width - r}Q${x + width},${top} ${x + width},${top + r}V100Z`;
}
function CollectionBars({ week, open }: { week: WeekDay[]; open: () => void }) {
  const heights = barHeights(week);
  return (
    <button
      className="holdings-curve collection-bars"
      onClick={open}
      aria-label="appearances each day this week. Open collection by day"
    >
      <span className="bars-caption">appearances each day, this week</span>
      <svg viewBox="0 0 700 104" aria-hidden="true">
        <line className="baseline" x1="0" x2="700" y1="100.5" y2="100.5" />
        {week.map((day, i) => {
          const height = heights[i];
          const x = i * 100 + 22;
          // A day before collection began, a day to come or a day with nothing draws no bar.
          if (!height || day.entries === null)
            return (
              <line
                key={day.day}
                className="slot"
                x1={x}
                x2={x + 56}
                y1="100.5"
                y2="100.5"
              />
            );
          return (
            <path
              key={day.day}
              className={day.today ? "bar today" : "bar"}
              d={barPath(x, 56, Math.max(2, height * 96))}
            >
              <title>{`${dayName(day.day)}${day.today ? ", so far" : ""} · ${whole(day.entries)} appearances`}</title>
            </path>
          );
        })}
      </svg>
      <span className="bars-days">
        {week.map((day, i) => (
          <span
            key={day.day}
            className={day.today ? "today" : i === 0 ? "first" : undefined}
          >
            {day.today ? "today · so far" : weekdayName(day.day)}
          </span>
        ))}
      </span>
    </button>
  );
}
// Where a source stands, in one line. Access comes first, and only a real read carries a date.
export function rightsStatus(
  entry: RightsEntry,
  own: boolean,
  now = Date.now(),
) {
  const source = entry.source;
  if (own) return groupNames.own;
  return honesty(source, now).label;
}
// A source in the rights register: what it is, whether it is read now, and what it allows.
function RightsSource({ entry, own }: { entry: RightsEntry; own: boolean }) {
  const status = rightsStatus(entry, own);
  return (
    <Hover
      label={entry.name}
      tap
      card={
        <>
          {entry.plain && <p className="hover-line">{entry.plain}</p>}
          <p className="how-meta">{status}</p>
          <p className="how-meta">{permissionLine(entry)}</p>
        </>
      }
    >
      <button type="button" className="rights-source">
        {entry.name}
      </button>
    </Hover>
  );
}
function unpricedLine(data: HoldingsData) {
  return data.vendor_cost.has_current_rows
    ? "Vendor requests are counted, but none carries a price yet. Hosting is excluded."
    : "No vendor usage is recorded this week. Hosting is excluded.";
}
export function Holdings({
  data,
  rights,
  provenance,
  liveProvenance,
  rightsProvenance,
  sources,
  view = "holds",
}: {
  data: HoldingsData;
  rights?: Rights;
  provenance: Provenance;
  // Sources live: the sources Rights lists as Collecting now.
  liveProvenance?: Provenance;
  rightsProvenance?: Provenance;
  sources: string | null;
  queries: { inventory: string };
  view?: HoldingsView;
}) {
  const tending = useTending();
  const tendingBox = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState(
    view === "rights" || view === "sources" ? "rights" : "holds",
  );
  const [details, setDetails] = useState(view === "collection");
  const groups = rightsGroups(rights?.sources ?? [], tending?.sources ?? null);
  const [group, setGroup] = useState<Group | null>(
    view === "sources" ? (groups.measured ? "collecting" : "all") : null,
  );
  const [offset, setOffset] = useState(0);
  // Sample loads from sources waiting for provider access are not collection.
  const collected = counted(data.ingestion.rows);
  const rows = BigInt(collected.week);
  const began = collectionBegan(tending?.sources ?? []);
  const week = weekDays(data.since, collected.days, { began });
  const firstDay = week.find((day) => day.before)
    ? week.find((day) => !day.before)
    : undefined;
  const peak = peakDay(week, collected.rows);
  const peakSource = tending?.sources.find(
    (source) => source.source_key === peak?.source_key,
  );
  const cents = BigInt(data.vendor_cost.cost_cents);
  // Cost shows only once vendor usage carries a price; free and flat-fee reads have none.
  const perThousand =
    data.vendor_cost.has_current_rows && cents > 0n && rows > 0n
      ? ((Number(cents) * 10) / Number(rows)).toFixed(2)
      : null;
  const costProvenance: Provenance = {
    ...provenance,
    sources: undefined,
    query: `Vendor charges recorded this week, $${(Number(cents) / 100).toFixed(2)} (${data.vendor_cost.label}), divided by ${whole(rows)} appearances and scaled to a thousand. Hosting is excluded.`,
    plain: "What vendors charged for every thousand appearances collected.",
  };
  const listed = group ? groups[group] : [];
  const tabs: Group[] = groups.measured
    ? measuredGroups.filter(
        (item) => item === "collecting" || groups[item].length,
      )
    : ["all", ...(groups.own.length ? (["own"] as const) : [])];
  const openGroup = (next: Group) => {
    setGroup(next);
    setOffset(0);
  };
  return (
    <section className="holdings-room">
      <div className="tabs" role="tablist" aria-label="Sources">
        <button
          role="tab"
          aria-selected={tab === "holds"}
          onClick={() => setTab("holds")}
        >
          Sources
        </button>
        <button
          role="tab"
          aria-selected={tab === "rights"}
          onClick={() => setTab("rights")}
        >
          Rights
        </button>
      </div>
      {tab === "holds" ? (
        <>
          <h1>what’s collected.</h1>
          <div className="holdings-numbers">
            {rows > 0n ? (
              <Metric
                value={rows.toLocaleString("en-US")}
                label="collected this week"
                provenance={provenance}
              />
            ) : (
              <p className="unmeasured">Collection · not measured yet</p>
            )}
            {sources && BigInt(sources) > 0n ? (
              <Metric
                value={whole(sources)}
                label="sources live"
                provenance={liveProvenance ?? provenance}
              />
            ) : (
              <p className="unmeasured">Sources · not measured yet</p>
            )}
            {perThousand !== null ? (
              <Metric
                value={`$${perThousand}`}
                label="per thousand appearances"
                provenance={costProvenance}
              />
            ) : (
              <Hover
                label="Cost"
                tap
                card={<p className="hover-line">{unpricedLine(data)}</p>}
              >
                <button type="button" className="unmeasured">
                  Cost · not measured yet
                </button>
              </Hover>
            )}
          </div>
          <CollectionBars week={week} open={() => setDetails(true)} />
          {perThousand !== null && (
            <p className="cost-note">
              {data.vendor_cost.label} · Hosting excluded
            </p>
          )}
          <div ref={tendingBox}>
            <Tending />
          </div>
        </>
      ) : (
        <>
          <h1>what’s allowed, and what isn’t.</h1>
          <Metric
            value={rights ? whole(rights.annotated) : null}
            label="sources on record"
            provenance={rightsProvenance}
          />
          <p className="rights-caption">
            Permissions say whether data is cleared for training or resale.
          </p>
          <div className="chips rights-groups">
            {groups.measured ? (
              <>
                <button onClick={() => openGroup("collecting")}>
                  {groupNames.collecting} · {groups.collecting.length}
                </button>
                {(["quiet", "off", "waiting"] as const)
                  .filter((item) => groups[item].length)
                  .map((item) => (
                    <button key={item} onClick={() => openGroup(item)}>
                      {groupNames[item]}
                    </button>
                  ))}
              </>
            ) : (
              <button onClick={() => openGroup("all")}>
                Collection · not measured yet
              </button>
            )}
          </div>
        </>
      )}
      {details && (
        <Sheet title="Collection by day" close={() => setDetails(false)}>
          <p>appearances collected each UTC day this week.</p>
          {firstDay && <p>Collection began {dayName(firstDay.day)}.</p>}
          {peak && peakSource?.cadence === "weekly" && (
            <p>
              {dayName(peak.day)} is highest because {peakSource.display_name}{" "}
              is read once a week.
            </p>
          )}
          <ul className="collection-days">
            {week.map((day) =>
              day.entries === null ? null : (
                <li key={day.day}>
                  <span>
                    {dayName(day.day)}
                    {day.today ? " · so far" : ""}
                  </span>
                  <strong>{whole(day.entries)}</strong>
                </li>
              ),
            )}
          </ul>
          {/* Old API snapshots also lack evidence that synthetic rows are excluded. */}
          <p className="unmeasured">Stored totals · not measured yet</p>
          <button
            className="primary"
            onClick={() => {
              setDetails(false);
              tendingBox.current?.scrollIntoView({ block: "start" });
            }}
          >
            What we tend ↓
          </button>
        </Sheet>
      )}
      {group && (
        <Sheet title="Sources" close={() => setGroup(null)}>
          <p className="rights-permits">
            <Metric
              inline
              value={rights ? whole(rights.learning) : null}
              label="cleared for training"
              provenance={rightsProvenance}
            />{" "}
            <Metric
              inline
              value={rights ? whole(rights.resale) : null}
              label="cleared for resale"
              provenance={rightsProvenance}
            />
          </p>
          <div className="tabs" role="tablist" aria-label="Sources">
            {tabs.map((item) => (
              <button
                key={item}
                role="tab"
                aria-selected={group === item}
                onClick={() => openGroup(item)}
              >
                {groupNames[item]} · {groups[item].length}
              </button>
            ))}
          </div>
          <p>{groupLines[group]}</p>
          <div className="held-sources">
            {listed.slice(offset, offset + page).map((entry) => (
              <RightsSource
                key={entry.source_key}
                entry={entry}
                own={group === "own"}
              />
            ))}
          </div>
          <div className="sheet-links rights-pages">
            {offset > 0 && (
              <button onClick={() => setOffset(offset - page)}>
                Previous sources
              </button>
            )}
            {listed.length > offset + page && (
              <button onClick={() => setOffset(offset + page)}>
                More sources
              </button>
            )}
          </div>
        </Sheet>
      )}
    </section>
  );
}
