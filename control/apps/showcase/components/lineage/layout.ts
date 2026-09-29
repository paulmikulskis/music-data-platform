import type { TraceView } from "../../lib/trace";
export const stages = [
  { id: "source", label: "Source" },
  { id: "readers", label: "Collection job" },
  { id: "collected", label: "Received" },
  { id: "cleaned", label: "Cleaned and matched" },
  { id: "ready", label: "Ready to use" },
  { id: "screen", label: "Your number" },
];
export function stageOf(stage: string) {
  if (["staging", "intermediate", "reference"].includes(stage))
    return "cleaned";
  if (stage === "raw") return "collected";
  if (stage === "marts") return "ready";
  return stage;
}
// Shared received tables take their reader from the selected path, never another writer.
export function readerForNode(
  node: TraceView["nodes"][number],
  nodes: TraceView["nodes"],
  edges: TraceView["edges"],
) {
  const stage = stageOf(node.stage);
  if (stage === "readers") return node;
  if (stage !== "collected" && stage !== "source") return null;
  return (
    nodes.find(
      (reader) =>
        reader.stage === "readers" &&
        edges.some((edge) =>
          stage === "source"
            ? edge.from === node.id && edge.to === reader.id
            : edge.from === reader.id && edge.to === node.id,
        ),
    ) ?? null
  );
}

export function nearestCountedStep(
  node: TraceView["nodes"][number],
  nodes: TraceView["nodes"],
  path: string[],
) {
  const position = path.indexOf(node.id);
  return nodes
    .filter((candidate) => candidate.count && candidate.id !== node.id)
    .sort(
      (a, b) =>
        Math.abs(path.indexOf(a.id) - position) -
        Math.abs(path.indexOf(b.id) - position),
    )[0];
}
// Stable dependency paths, never a force simulation. One chain fits on a narrow screen.
export function chains(view: TraceView) {
  const sinks = view.nodes.filter((node) => node.stage === "screen");
  const ready = view.nodes.filter((node) => stageOf(node.stage) === "ready");
  const targets = sinks.length
    ? sinks
    : ready.length
      ? ready
      : view.nodes.filter(
          (node) => !view.edges.some((edge) => edge.from === node.id),
        );
  const starts = view.nodes.filter((node) => node.stage === "source");
  const paths: string[][] = [];
  for (const start of starts) {
    for (const target of targets) {
      const queue = [[start.id]];
      const seen = new Set([start.id]);
      while (queue.length) {
        const path = queue.shift()!;
        const last = path.at(-1);
        if (last === target.id) {
          paths.push(path);
          break;
        }
        const next = view.edges
          .filter((edge) => edge.from === last)
          .sort((a, b) => Number(b.lit) - Number(a.lit));
        for (const edge of next) {
          if (seen.has(edge.to)) continue;
          seen.add(edge.to);
          queue.push([...path, edge.to]);
        }
      }
    }
  }
  return paths.sort(
    (a, b) =>
      b.filter((id) => view.nodes.some((node) => node.id === id && node.lit))
        .length -
      a.filter((id) => view.nodes.some((node) => node.id === id && node.lit))
        .length,
  );
}
