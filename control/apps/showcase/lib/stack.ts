import { z } from "zod";
import sensitivePatternSources from "./sensitive-patterns.json";
import linkedContentPolicy from "./linked-content-policy.json";

const hash = z.string().regex(/^[a-f0-9]{64}$/);
const date = z.string().datetime({ offset: true });
const count = z.number().int().nonnegative().nullable();
const probe = z
  .object({
    state: z.enum(["not_checked", "answered", "no_answer"]),
    name: z.enum([
      "Not checked",
      "Console HTTP health",
      "Runner state, last rounds and rebuilt tables",
    ]),
    checked_at: date.nullable(),
  })
  .strict()
  .refine(
    (value) => value.state === "not_checked" || value.checked_at !== null,
  );
export const stackSchema = z
  .object({
    schema_version: z.literal(1),
    input_hashes: z.record(z.string(), hash),
    captured_at: date,
    services: z.array(
      z
        .object({
          alias: z.string(),
          kind: z.enum(["Server", "Database", "On a timer"]),
          process_role: z.string(),
          region: z.enum(["New Jersey", "Not checked"]),
          machine_count: count,
          cpu_total: count,
          memory_mb_total: count,
          storage_gb_total: count,
          encrypted: z.boolean().nullable(),
          measured_at: date.nullable(),
          public_link: z.string().url().nullable(),
          status: probe,
          facts: z.array(
            z
              .object({
                text: z.string(),
                captured_at: date,
                evidence: z.string(),
              })
              .strict(),
          ),
        })
        .strict(),
    ),
  })
  .strict();
export const buildEnvelopeSchema = z
  .object({
    schema_version: z.literal(1),
    revision: z.string().regex(/^[a-f0-9]{40}$/),
    lineage_hash: hash,
    stack_hash: hash,
    links_hash: hash,
    lineage_inputs: z.record(z.string(), hash),
    stack_inputs: z.record(z.string(), hash),
    links_inputs: z.record(z.string(), hash),
  })
  .strict();
export type Stack = z.infer<typeof stackSchema>;
export function readStack(value: unknown) {
  return stackSchema.parse(value);
}

// Strings that must never reach a viewer screen: private addresses and hosts, deployment
// identifiers, volume and image references, secret names and anything shaped like a secret,
// and unreviewed infrastructure. The artifact allowlist
// keeps them out; this scan refuses an artifact or a status note that carries one anyway.
const opaquePolicy = linkedContentPolicy.opaque_token;
const opaqueHash = new RegExp(opaquePolicy.hash);
const opaqueSlug = new RegExp(opaquePolicy.slug);
const longNumericWord = new RegExp(opaquePolicy.long_numeric_word);
const secretWords = new Set(opaquePolicy.secret_words);
function secretContext(value: string): boolean {
  const words = value
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .match(/[a-z]+/g);
  return (words ?? []).some(
    (word) =>
      secretWords.has(word) ||
      (word.endsWith("s") && secretWords.has(word.slice(0, -1))),
  );
}
function opaqueToken(value: string): boolean {
  if (opaqueHash.test(value)) return false;
  if (opaqueSlug.test(value) && !longNumericWord.test(value)) return false;
  const bare = value.replace(/=+$/, "");
  if (
    bare.length >= opaquePolicy.long_alphanumeric_length &&
    /^[A-Za-z0-9]+$/.test(bare)
  )
    return true;
  if (
    (/^[A-Za-z0-9]+$/.test(bare) ||
      value.includes("+") ||
      value.includes("=")) &&
    /[a-z]/.test(value) &&
    /[A-Z]/.test(value) &&
    /[0-9]/.test(value)
  )
    return true;
  const counts = new Map<string, number>();
  for (const character of value)
    counts.set(character, (counts.get(character) ?? 0) + 1);
  let entropy = 0;
  for (const count of counts.values()) {
    const frequency = count / value.length;
    entropy -= frequency * Math.log2(frequency);
  }
  const threshold = Math.min(
    opaquePolicy.minimum_entropy,
    Math.log2(value.length) - opaquePolicy.short_token_margin,
  );
  return entropy >= threshold;
}
const sensitivePatterns = [
  ...sensitivePatternSources,
  ...linkedContentPolicy.hard,
].map((pattern) => {
  const opaque = "kind" in pattern && pattern.kind === "opaque";
  return {
    expression: new RegExp(
      pattern.pattern,
      pattern.flags + (opaque ? "g" : ""),
    ),
    opaque,
  };
});
function sensitiveValues(
  value: unknown,
  path: string,
  secret: boolean,
): string[] {
  if (typeof value === "string")
    return sensitivePatterns.some(({ expression, opaque }) => {
      if (!opaque) return expression.test(value);
      const contents = secret
        ? [value]
        : value.split(/\r?\n/).filter(secretContext);
      for (const content of contents)
        for (const match of content.matchAll(expression))
          if (opaqueToken(match[0])) return true;
      return false;
    })
      ? [path]
      : [];
  if (Array.isArray(value))
    return value.flatMap((item, index) =>
      sensitiveValues(item, `${path}[${index}]`, secret),
    );
  if (value !== null && typeof value === "object")
    return Object.entries(value).flatMap(([key, item]) =>
      sensitiveValues(item, `${path}.${key}`, secret || secretContext(key)),
    );
  return [];
}
export function sensitiveStrings(value: unknown, path = "$"): string[] {
  return sensitiveValues(value, path, false);
}

// The runtime status read for the Stack page. Each service names its probe and time, or says
// that nothing checked it. Copy on the page never infers job success from an HTTP answer.
export const stackStatus = z.object({
  checked_at: date,
  services: z.array(
    z
      .object({
        alias: z.string(),
        state: z.enum(["answered", "no_answer", "not_checked"]),
        probe: z.string(),
        checked_at: date.nullable(),
        note: z.string(),
      })
      .strict(),
  ),
});
export type StackStatus = z.infer<typeof stackStatus>;
