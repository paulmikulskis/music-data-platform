import "server-only";
import { readLinks } from "./links";
import { linkHref } from "../lib/links";
import { z } from "zod";
import { encodeWire } from "@mdp/data-sdk";
import { controlStore } from "./clients";
import { budget } from "./read-budget";
import type { Person } from "@mdp/showcase-auth";
import { lineage, resolveFactPath } from "../lib/lineage";
import { traceSelection, type TraceView } from "../lib/trace";
import { musicFacts } from "../lib/music-facts";
import { sourceWording } from "@mdp/contracts/source-wording";
import { sourceFunctionState } from "../lib/function-copy";
import { capturedProof } from "./proof-store";
import { movers } from "./reads";
import { sources } from "./platform";
import { lineageCounts } from "./lineage-counts";
import { traceReceipts } from "./trace-evidence";
import { buildStamp, relationBuildKey } from "./relation-counts";
import { readArtifacts } from "./artifacts";

const schedules = budget.cache<{ cadence: string; at: string | null }[]>();
async function readerSchedules() {
  const result = await schedules.read("trace-schedules", "light", async () =>
    z.array(z.object({ cadence: z.string(), at: z.string().nullable() })).parse(
      encodeWire(
        await controlStore()`SELECT cadence,
        (CASE WHEN cadence='daily' THEN
          (date_trunc('day',now() AT TIME ZONE timezone) + make_interval(hours=>due_hour)) AT TIME ZONE timezone
        WHEN cadence='weekly' THEN
          (date_trunc('week',now() AT TIME ZONE timezone) + make_interval(days=>due_weekday-1,hours=>due_hour)) AT TIME ZONE timezone
        END) AS at FROM control.dbt_job WHERE scope='global' AND runner='core'`,
      ),
    ),
  );
  return result.value;
}
function readerName(name: string) {
  return `${name.replace(/ playlists.*$/i, " playlist").replace(/charts$/i, "chart")} reader`;
}
function contents(relation: string | null) {
  if (relation?.includes("playlist")) return "Matched playlists";
  if (relation?.includes("shazam")) return "Matched Shazam songs";
  if (relation?.includes("account")) return "Public account counts";
  if (relation?.includes("artist")) return "Matched artists";
  if (relation?.includes("track") || relation?.includes("song"))
    return "Matched songs";
  return "Collected records";
}
export async function traceData(
  person: Person,
  value: unknown,
): Promise<TraceView> {
  const selection = traceSelection.parse(value);
  const source = selection.entry.startsWith("source.")
    ? selection.entry.slice(7)
    : null;
  const entries =
    selection.entry === "home.mover_card"
      ? lineage.entries
      : lineage.entries.filter((entry) => entry.id === selection.entry);
  const sourceNode = source
    ? lineage.nodes.find((node) => node.id === `fn:${source}`)
    : null;
  if (!entries.length && !sourceNode)
    return {
      state: "unknown",
      title: "Sources",
      fact: null,
      revision: null,
      nodes: [],
      edges: [],
    };
  let row = capturedProof(selection.ranking ?? "", selection.song ?? "");
  if (!row && selection.song && selection.ranking) {
    await movers(50).catch(() => null);
    row = capturedProof(selection.ranking, selection.song);
  }
  const resolutions: ReturnType<typeof resolveFactPath>[] = [];
  for (const entry of entries) {
    const selected = { ...selection, entry: entry.id };
    const receipts = row ? await traceReceipts(selected, row) : [];
    resolutions.push(
      resolveFactPath(
        {
          entry: entry.id,
          song_key: selection.song ?? "",
          ranking_build: selection.ranking ?? "",
        },
        row,
        lineage,
        receipts,
      ),
    );
  }
  const litEdges = resolutions.flatMap((result) => result.edges);
  const lit = new Set(litEdges.flatMap((edge) => [edge.from, edge.to]));
  const endpoints = sourceNode
    ? []
    : entries.flatMap((entry) =>
        entry.selectors.map((rule) => `rel:${rule.relation}`),
      );
  // Keep actual dependency paths. This walk never changes whether an edge is evidenced.
  const wanted = new Set<string>(endpoints);
  if (sourceNode) {
    wanted.add(sourceNode.id);
    const queue = [sourceNode.id];
    while (queue.length) {
      const id = queue.shift();
      for (const edge of lineage.edges.filter(
        (edge) => edge.from === id && edge.kind !== "evidence",
      )) {
        if (wanted.has(edge.to)) continue;
        wanted.add(edge.to);
        if (
          !lineage.nodes.some(
            (node) => node.id === edge.to && node.stage === "ready",
          )
        )
          queue.push(edge.to);
      }
    }
    for (const edge of lineage.edges.filter(
      (edge) => edge.to === sourceNode.id,
    ))
      wanted.add(edge.from);
  } else {
    const queue = [...endpoints];
    while (queue.length) {
      const id = queue.shift();
      for (const edge of lineage.edges.filter(
        (edge) => edge.to === id && edge.kind !== "evidence",
      )) {
        if (!wanted.has(edge.from)) {
          wanted.add(edge.from);
          queue.push(edge.from);
        }
      }
    }
    for (const entry of entries) wanted.add(`entry:${entry.id}`);
  }
  const available = lineage.nodes.filter((node) => wanted.has(node.id));
  const live = await sources(person).catch(() => null);
  const schedule = await readerSchedules().catch(() => []);
  const relations = available.flatMap((node) =>
    node.relation ? [node.relation] : [],
  );
  const counts = await lineageCounts(relations).catch(() => []);
  let revision: string | null = null;
  try {
    revision = readArtifacts().build.revision;
  } catch {
    /* No unverified code link. */
  }
  const links = readLinks();
  const nodes = available.map((node) => {
    const reader =
      node.kind === "function"
        ? node.id.slice(3)
        : lineage.edges
            .find((edge) => edge.from === node.id && edge.kind === "declares")
            ?.to.slice(3);
    const current = live?.value.sources.find(
      (item) => item.source_key === reader,
    );
    const wording = reader ? sourceWording[reader] : null;
    const evidence = resolutions
      .flatMap((result) => result.evidence)
      .find(
        (item) =>
          `marts.${item.relation.replace(/^marts\./, "")}` === node.relation,
      );
    const spec = node.relation ? lineage.peeks[node.relation] : null;
    const filters: Record<string, string | number | boolean> = {};
    if (spec && evidence)
      for (const key of spec.filters) {
        const value = evidence.row_key[key];
        if (value !== null && value !== undefined) filters[key] = value;
      }
    const component = node.id.replace("entry:home.", "");
    const value = row?.score_parts.find(
      (part) => part.component === component,
    )?.value;
    const number =
      node.kind !== "fact" || !row
        ? null
        : component === "playlist_adds" && row.playlist_count !== null
          ? `${BigInt(row.playlist_count).toLocaleString("en-US")} ${row.playlist_count === "1" ? "playlist" : "playlists"}`
          : value === undefined
            ? null
            : component === "follower_exposure_gain"
              ? `${Math.round(value).toLocaleString("en-US")} followers`
              : component === "stream_rate_gain"
                ? `+${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}% plays`
                : component === "shazam_spread_gain"
                  ? `${Math.round(value).toLocaleString("en-US")} more places`
                  : null;
    const count = counts.find((item) => item.relation === node.relation);
    const countedBuild = count ? decodeCountBuild(count.build_key) : null;
    const code = node.file ? links.get("viewer-node-code", node.file) : null;
    const explorer =
      spec &&
      node.relation &&
      /^(staging|intermediate|marts)\./.test(node.relation)
        ? links.get("viewer-open-explorer")
        : null;
    const previews = [
      code,
      spec?.workbench ? links.get("viewer-open-workbench") : null,
      explorer && node.relation
        ? {
            ...explorer,
            destination: {
              route: `/explorer?${new URLSearchParams({ q: node.relation })}`,
              back: "/sources",
            },
          }
        : null,
      node.kind === "function" ? links.get("viewer-job-stack") : null,
      node.kind === "function" && reader
        ? links.get("proof-open-console", reader)
        : null,
    ].filter((preview) => preview !== null);
    return {
      id: node.id,
      label:
        node.kind === "function"
          ? readerName(wording?.name ?? "Source")
          : node.tier === 1
            ? node.label
            : contents(node.relation),
      short:
        node.kind === "function"
          ? readerName(wording?.name ?? "Source")
          : (number ?? node.short_label ?? contents(node.relation)),
      stage: node.stage,
      relation: node.relation,
      lit: lit.has(node.id),
      cadence: current?.cadence ?? node.cadence,
      scheduled_at:
        schedule.find(
          (item) => item.cadence === (current?.cadence ?? node.cadence),
        )?.at ?? null,
      source: reader ?? source,
      brand: current?.last_read ? current.brand : null,
      last_read: current?.evidence?.last_success ?? null,
      enabled: current?.enabled ?? null,
      paused: sourceFunctionState(current).paused ?? false,
      count:
        count && countedBuild
          ? {
              value: count.row_count,
              captured_at: count.captured_at,
              build_key: count.build_key,
              build: {
                ...countedBuild,
                scope: "global" as const,
                stamped: true as const,
                tenant_slug: null,
              },
            }
          : null,
      filters,
      expected: evidence
        ? relationBuildKey(
            buildStamp.parse({
              ...evidence.input_build,
              relation: `marts.${evidence.input_build.relation.replace(/^marts\./, "")}`,
            }),
          )
        : null,
      preview: !!node.relation && !!lineage.peeks[node.relation],
      code: code ? linkHref(code) : null,
      links: previews,
    };
  });
  return {
    state: sourceNode
      ? "source"
      : litEdges.length
        ? "evidenced"
        : "unavailable",
    title: row?.title_text ?? "Data sources",
    fact: row
      ? (musicFacts(row).find(
          (fact) => `home.${fact.component}` === selection.entry,
        )?.text ?? null)
      : null,
    revision,
    nodes,
    edges: lineage.edges
      .filter((edge) => wanted.has(edge.from) && wanted.has(edge.to))
      .map((edge) => ({
        from: edge.from,
        to: edge.to,
        lit: litEdges.some(
          (lit) => lit.from === edge.from && lit.to === edge.to,
        ),
      })),
  };
}

function decodeCountBuild(key: string) {
  try {
    const [relation, cycle_id, close_no, built_at] = z
      .tuple([z.string(), z.string(), z.string().nullable(), z.string()])
      .parse(JSON.parse(key));
    return buildStamp.parse({ relation, cycle_id, close_no, built_at });
  } catch {
    return null;
  }
}
