import "server-only";
import { readArtifacts } from "./artifacts";
import { readLinks } from "./links";
import type { LinkPreview } from "../lib/links";
import { sensitiveStrings, type Stack } from "../lib/stack";
import { datedFacts, latestFactDate, type DatedFact } from "../lib/stack-facts";
import {
  stackCards,
  type StackCard,
  type StackGroup,
} from "../lib/stack-cards";

export type StackServiceView = {
  alias: string;
  id: string;
  group: StackGroup;
  kind: StackCard["kind"];
  name: string;
  tagline: string;
  marks: string[];
  code: LinkPreview | null;
  open: LinkPreview | null;
  links: LinkPreview[];
  more: string[];
  tie: string | null;
  facts: DatedFact[];
  count: { text: string; as_of: string; measured: boolean } | null;
  region: string | null;
  storage: { text: string; measured_at: string } | null;
};
// The data platform's measured shape for the avatar sheet: machines by kind, counted from the
// deploy-time machine list. Null unless every platform service carries a measured count.
export type StackShape = {
  databases: number;
  servers: number;
  timers: number;
  as_of: string;
};
export type StackView = {
  mode: "generated" | "dated";
  notice: string | null;
  as_of: string;
  revision: string | null;
  services: StackServiceView[];
  shape: StackShape | null;
};

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}
function measuredCount(service: Stack["services"][number]) {
  if (service.machine_count === null) return null;
  const n = service.machine_count;
  if (service.kind === "Database")
    return plural(n, "database server", "database servers");
  if (service.kind === "On a timer")
    return `${plural(n, "server", "servers")} on a timer`;
  return plural(n, "server", "servers");
}
function view(
  card: StackCard,
  service: Stack["services"][number] | null,
  facts: DatedFact[],
  links: ReturnType<typeof readLinks>,
): StackServiceView {
  const measured = service ? measuredCount(service) : null;
  const storage =
    service?.storage_gb_total !== null &&
    service?.storage_gb_total !== undefined &&
    service.storage_gb_total > 0 &&
    service.measured_at
      ? {
          text: `${service.storage_gb_total} GB ${service.encrypted ? "encrypted " : ""}disk`,
          measured_at: service.measured_at,
        }
      : null;
  return {
    alias: card.alias,
    id: card.id,
    group: card.group,
    kind: card.kind,
    name: card.name,
    tagline: card.tagline,
    marks: card.marks,
    code: links.get(`stack-code-${card.id}`),
    open: links.get(`stack-open-${card.id}`),
    links: (card.id === "warehouse"
      ? ["more-served-doc"]
      : card.id === "readers"
        ? ["more-source-list"]
        : card.id === "workbench"
          ? ["more-workbench-guide"]
          : []
    ).flatMap((id) => {
      const preview = links.get(id);
      return preview ? [preview] : [];
    }),
    more: card.more,
    tie: card.tie,
    // A measured disk size replaces the dated disk fact, so one fact never shows twice.
    facts: storage
      ? facts.filter((fact) => !/\bdisk\b/i.test(fact.text))
      : facts,
    count:
      measured && service?.measured_at
        ? { text: measured, as_of: service.measured_at, measured: true }
        : card.count
          ? { ...card.count, measured: false }
          : null,
    region:
      service?.region === "New Jersey" && service.measured_at
        ? service.region
        : null,
    storage,
  };
}
function shapeOf(stack: Stack): StackShape | null {
  const platform = stackCards.filter((card) => card.group === "platform");
  const measured = platform.map((card) =>
    stack.services.find((service) => service.alias === card.alias),
  );
  const shape = {
    databases: 0,
    servers: 0,
    timers: 0,
    as_of: stack.captured_at,
  };
  for (const service of measured) {
    if (!service || service.machine_count === null || !service.measured_at)
      return null;
    if (service.kind === "Database") shape.databases += service.machine_count;
    else if (service.kind === "On a timer")
      shape.timers += service.machine_count;
    else shape.servers += service.machine_count;
    if (service.measured_at < shape.as_of) shape.as_of = service.measured_at;
  }
  return shape;
}
// The deploy artifact supplies measured counts and the deployed revision. Without it, or when
// it fails its checks, the cards render from the dated hand facts and say so.
export function stackView(
  load: () => ReturnType<typeof readArtifacts> = readArtifacts,
  links: ReturnType<typeof readLinks> = readLinks(),
): StackView {
  let loaded: ReturnType<typeof readArtifacts> | null = null;
  try {
    loaded = load();
  } catch {
    loaded = null;
  }
  const unsafe = loaded ? sensitiveStrings(loaded.stack.services) : [];
  if (loaded && unsafe.length === 0) {
    const { stack, build } = loaded;
    return {
      mode: "generated",
      notice: null,
      as_of: stack.captured_at,
      revision: build.revision,
      services: stackCards.map((card) =>
        view(
          card,
          stack.services.find((service) => service.alias === card.alias) ??
            null,
          stack.services.find((service) => service.alias === card.alias)
            ?.facts ??
            datedFacts[card.alias] ??
            [],
          links,
        ),
      ),
      shape: shapeOf(stack),
    };
  }
  return {
    mode: "dated",
    notice: "Counts not refreshed at this deploy.",
    as_of: latestFactDate() ?? new Date(0).toISOString(),
    revision: null,
    services: stackCards.map((card) =>
      view(card, null, datedFacts[card.alias] ?? [], links),
    ),
    shape: null,
  };
}
