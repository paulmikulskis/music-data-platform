import { isMusicSource } from "./source-families";
import { lineage } from "./lineage";

export const reviewedSources = lineage.nodes
  .filter((node) => node.kind === "source" && node.tier === 1)
  .map((node) => ({
    ...node,
    readers: lineage.edges
      .filter((edge) => edge.from === node.id && edge.to.startsWith("fn:"))
      .map((edge) => edge.to.slice(3)),
  }))
  .filter((source) =>
    source.readers.some((source_key) => isMusicSource({ source_key })),
  );
export const reviewedReaderKeys = new Set(
  reviewedSources.flatMap((source) => source.readers),
);
