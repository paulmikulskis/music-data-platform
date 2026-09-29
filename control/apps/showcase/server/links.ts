import "server-only";
import sourceRegistry from "../lib/source-registry.generated.json";
import { lineage } from "../lib/lineage";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import manifestValue from "../../../../ops/showcase/links/links.json";
import {
  linkFactSchema,
  linksHeaderSchema,
  linksManifestSchema,
  type LinkPreview,
} from "../lib/links";
import { buildEnvelopeSchema, sensitiveStrings } from "../lib/stack";
import { artifactDirectory, artifactHash } from "./artifacts";

const manifest = linksManifestSchema.parse(manifestValue);
const accessLines = {
  "code":
    "Public source. GitHub shows contributor handles.",
  invite: "Needs an invite from the data team to see data.",
  gateway: "Operator login only. Ask the data team.",
  session: "",
  "in-app": "",
  public: "",
};
function read(directory: string, name: string): unknown {
  return JSON.parse(readFileSync(join(directory, name), "utf8"));
}
function day(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}
// Read only links and the envelope. Stack and lineage have independent readers.
export function readLinks(directory = artifactDirectory()) {
  let buildRevision: string | null = null;
  let header: ReturnType<typeof linksHeaderSchema.parse> | null = null;
  try {
    const build = buildEnvelopeSchema.parse(
      read(directory, "artifacts.build.json"),
    );
    buildRevision = build.revision;
    const raw = read(directory, "links.generated.json");
    const parsed = linksHeaderSchema.parse(raw);
    if (
      artifactHash(raw) === build.links_hash &&
      parsed.revision === build.revision &&
      artifactHash(parsed.input_hashes) === artifactHash(build.links_inputs)
    ) {
      header = parsed;
    }
  } catch {
    // A missing preview never changes the result of another artifact reader.
  }
  function expected(id: string, variant: string) {
    const definition = manifest.links.find((link) => link.id === id);
    if (!definition) return null;
    const destination = definition.destination;
    if (id === "viewer-node-code" && "repo" in destination) {
      return lineage.nodes.some((node) => node.file === variant)
        ? { ...destination, path: variant }
        : null;
    }
    if (id === "sources-reader-code" && "repo" in destination) {
      return Object.entries(sourceRegistry).some(
        ([key, value]) =>
          key === variant &&
          value.source &&
          lineage.nodes.some((node) => node.id === `fn:${key}`),
      )
        ? { ...destination, path: destination.path.replace("<key>", variant) }
        : null;
    }
    if (id === "proof-open-console") {
      return Object.entries(sourceRegistry).some(
        ([key, value]) =>
          key === variant &&
          value.source &&
          lineage.nodes.some((node) => node.id === `fn:${key}`),
      )
        ? { route: `/functions/${variant}`, back: "/sources" }
        : null;
    }
    if (id === "team-desk-anchors") {
      const routes: Record<string, string> = {
        cylinder: "/stack#warehouse",
        laptop: "/team#practice",
        screen: "/team#screen",
      };
      const route = routes[variant];
      return Object.hasOwn(routes, variant) && typeof route === "string"
        ? { route, back: null }
        : null;
    }
    if (id === "home-steps") {
      return ["postgresql", "r", "python", "dbt"].includes(variant)
        ? {
            route: variant === "dbt" ? "/team#screen" : "/team#first-question",
            back: null,
          }
        : null;
    }
    return variant === "" ? destination : null;
  }
  const entries = new Map<string, ReturnType<typeof linkFactSchema.parse>>();
  for (const raw of header?.entries ?? []) {
    const parsed = linkFactSchema.safeParse(raw);
    if (
      parsed.success &&
      sensitiveStrings(parsed.data).length === 0 &&
      artifactHash(parsed.data.destination) ===
        artifactHash(expected(parsed.data.id, parsed.data.variant))
    ) {
      entries.set(`${parsed.data.id}:${parsed.data.variant}`, parsed.data);
    }
  }
  return {
    get(id: string, variant = ""): LinkPreview | null {
      const definition = manifest.links.find((link) => link.id === id);
      if (!definition) return null;
      const entry = entries.get(`${id}:${variant}`);
      const destination =
        entry?.destination ?? expected(id, variant) ?? definition.destination;
      const code = "repo" in destination;
      const revision = code
        ? buildRevision
        : null;
      const unresolved = JSON.stringify(destination).includes("<");
      const available = Boolean(entry) && (!code || Boolean(revision));
      const frame: string[] = [];
      if (available && entry) {
        if (code) {
          frame.push("Source preview");
          frame.push(
            `Code at this page's build · rev ${revision?.slice(0, 7)}`,
          );
        } else if (definition.thumbnail === "door") {
          frame.push(
            entry.probe.checked_at
              ? `${entry.probe.note} · ${day(entry.probe.checked_at)}`
              : entry.probe.note,
          );
          frame.push("Not checked");
        } else if (definition.thumbnail === "page") {
          frame.push("Drawing, not the live page", "Not checked");
        }
      } else if (!code || revision) {
        frame.push("Preview not recorded at this deploy");
      }
      return {
        id,
        variant,
        label: definition.label,
        kind: available ? definition.thumbnail : "none",
        destination: (code && !revision) || unresolved ? null : destination,
        revision,
        objectType:
          entry?.object_type ??
          (code ? (definition.thumbnail === "folder" ? "tree" : "blob") : null),
        body: available ? (entry?.body ?? []) : [],
        properNames: available ? (entry?.proper_names ?? []) : [],
        frame,
        access: accessLines[definition.access],
        line: entry?.line ?? null,
        available,
      };
    },
  };
}
