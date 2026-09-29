import { z } from "zod";

const revision = z.string().regex(/^[0-9a-f]{40}$/);
const date = z.string().datetime({ offset: true });
const count = z.number().int().nonnegative().nullable();
export const linkDestinationSchema = z.union([
  z
    .object({
      repo: z.enum(["music-data-platform"]),
      path: z.string(),
      anchor: z.string().nullable(),
    })
    .strict(),
  z.object({ route: z.string(), back: z.string().nullable() }).strict(),
  z.object({ host: z.string() }).strict(),
]);
export const linkDefinitionSchema = z
  .object({
    id: z.string().regex(/^[a-z][a-z0-9-]+$/),
    label: z.string(),
    category: z.enum([
      "repo-path",
      "repo",
      "docs",
      "analyst-sql",
      "console",
      "deployed demo",
      "dashboard",
      "internal",
      "provider",
    ]),
    destination: linkDestinationSchema,
    access: z.enum([
      "code",
      "session",
      "invite",
      "gateway",
      "in-app",
      "public",
    ]),
    surfaces: z.array(z.string()).min(1),
    thumbnail: z.enum(["folder", "file", "doc", "page", "door", "none"]),
    nouns: z.array(z.string()).min(2).max(3),
    highlights: z.array(z.string()).max(3),
    headings: z.array(z.string()).max(3),
    line_marker: z.string().nullable(),
  })
  .strict();
export const linksManifestSchema = z
  .object({
    schema_version: z.literal(1),
    console_tenant_review: z.string().min(1).nullable(),
    links: z.array(linkDefinitionSchema),
  })
  .strict();
export const linkFactSchema = z
  .object({
    id: z.string(),
    variant: z.string(),
    destination: linkDestinationSchema,
    object_type: z.enum(["tree", "blob"]).nullable(),
    highlights: z.array(z.string()).max(3),
    listing_count: count,
    line_count: count,
    headings: z.array(z.string()).max(3),
    line: z.number().int().positive().nullable(),
    body: z.array(z.string()),
    proper_names: z.array(z.string()),
    probe: z
      .object({
        state: z.enum(["not_checked", "answered", "no_answer"]),
        checked_at: date.nullable(),
        note: z.enum([
          "Not checked at deploy",
          "Answered at deploy",
          "No answer at deploy",
        ]),
      })
      .strict(),
    names: z.enum(["checked", "names_not_checked"]),
    exempted_name_matches: z.number().int().nonnegative(),
  })
  .strict();
export const linksHeaderSchema = z
  .object({
    schema_version: z.literal(1),
    revision: revision.nullable(),
    collected_at: date,
    input_hashes: z.record(z.string(), z.string().regex(/^[0-9a-f]{64}$/)),
    names: z.enum(["checked", "names_not_checked"]),
    tenants: z.enum(["checked", "not_checked"]),
    entries: z.array(z.unknown()),
  })
  .strict();
export const linksSchema = linksHeaderSchema
  .extend({ entries: z.array(linkFactSchema) })
  .strict();
export type LinkDefinition = z.infer<typeof linkDefinitionSchema>;
export type LinkFact = z.infer<typeof linkFactSchema>;
export const linkPreviewSchema = z
  .object({
    id: z.string(),
    variant: z.string(),
    label: z.string(),
    kind: linkDefinitionSchema.shape.thumbnail,
    destination: linkDestinationSchema.nullable(),
    revision: revision.nullable(),
    objectType: linkFactSchema.shape.object_type,
    body: z.array(z.string()),
    properNames: z.array(z.string()),
    frame: z.array(z.string()),
    access: z.string(),
    line: z.number().int().positive().nullable(),
    available: z.boolean(),
  })
  .strict();
export type LinkPreview = z.infer<typeof linkPreviewSchema>;

// Resolve only the parts supplied by the server reader, when the link renders.
export function linkHref(preview: LinkPreview, back?: string) {
  const destination = preview.destination;
  if (!destination) return null;
  if ("repo" in destination) {
    if (!preview.revision || !preview.objectType) return null;
    const anchor = preview.line ? `L${preview.line}` : destination.anchor;
    return `https://github.com/${process.env.MDP_GITHUB_OWNER ?? "paulmikulskis"}/${destination.repo}/${preview.objectType}/${preview.revision}/${destination.path}${anchor ? `#${anchor}` : ""}`;
  }
  if ("host" in destination) return destination.host;
  if (destination.back === null) return destination.route;
  return `/s/open?${new URLSearchParams({ to: destination.route, back: back ?? destination.back })}`;
}
