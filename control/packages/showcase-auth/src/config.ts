import { z } from "zod";
const personSchema = z.object({
  handle: z.string().regex(/^[a-z][a-z0-9_-]{0,47}$/),
  display_name: z.string().min(1),
  email: z.email().optional(),
  admin_key: z.string().min(1),
  api_key_id: z.uuid(),
});
export type Person = z.infer<typeof personSchema>;
export function required(name: string): string {
  const value = process.env[name];
  if (!value)
    throw new Error(
      `Missing ${name}. Set it in the runtime environment and restart the app.`,
    );
  return value;
}
// Parse on each request: removal and key rotation take effect without an auth cache.
export function people(): Person[] {
  let decoded: unknown;
  try {
    decoded = JSON.parse(required("MDP_SHOWCASE_PEOPLE"));
  } catch {
    throw new Error(
      "MDP_SHOWCASE_PEOPLE is missing or invalid. Set a JSON list of people and restart the app.",
    );
  }
  const parsed = z.array(personSchema).safeParse(decoded);
  if (!parsed.success) {
    throw new Error(
      "MDP_SHOWCASE_PEOPLE has an invalid person. Check each handle, display_name, admin_key and api_key_id, then restart the app.",
    );
  }
  const result = parsed.data;
  if (
    new Set(result.map((p) => p.handle)).size !== result.length ||
    new Set(result.map((p) => p.api_key_id)).size !== result.length
  )
    throw new Error(
      "Duplicate showcase identity. Give each handle a separate admin key in MDP_SHOWCASE_PEOPLE.",
    );
  return result;
}
export function person(handle: string): Person | undefined {
  return people().find((p) => p.handle === handle);
}
export function origin(): string {
  const url = new URL(
    process.env.MDP_SHOWCASE_ORIGIN ?? "https://mdp-showcase.example.invalid",
  );
  if (
    url.protocol !== "https:" &&
    !(
      url.protocol === "http:" &&
      ["localhost", "127.0.0.1"].includes(url.hostname)
    )
  )
    throw new Error("Invalid showcase origin. Use HTTPS or a loopback URL.");
  return url.origin;
}

export function sessionLifetimes() {
  const days = (name: string, fallback: number) => {
    const raw = process.env[name];
    const value = raw === undefined ? fallback : Number(raw);
    if (
      (raw !== undefined && !/^[0-9]+$/.test(raw)) ||
      !Number.isInteger(value) ||
      value < 1 ||
      value > 365
    ) {
      throw new Error(
        `${name} must be a whole number from 1 to 365. Set it in the runtime environment and restart the app.`,
      );
    }
    return value;
  };
  const idleDays = days("MDP_SHOWCASE_IDLE_DAYS", 14);
  const maxDays = days("MDP_SHOWCASE_MAX_DAYS", 60);
  if (idleDays > maxDays) {
    throw new Error(
      "MDP_SHOWCASE_IDLE_DAYS exceeds MDP_SHOWCASE_MAX_DAYS. Set idle days at or below maximum days and restart the app.",
    );
  }
  return { idleDays, maxDays };
}
