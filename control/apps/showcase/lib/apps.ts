import { z } from "zod";
import publicHostValues from "./public-hosts.json";
const publicHosts: { name: string; host: string; health: string }[] = publicHostValues;
import { linkPreviewSchema } from "./links";
import { dayLabel } from "./presentation";
export const appList = z.array(
  z.object({
    name: z.string(),
    href: z
      .string()
      .refine(
        (value) =>
          value === "/ops" ||
          publicHosts.map((entry) => entry.host).includes(value),
      ),
    state: z.enum(["live", "unknown"]),
    checked_at: z.iso.datetime().nullable().default(null),
  }),
);
// The data platform's shape, measured at deploy from the machine list, or null when this deploy
// carries no measurement. The avatar sheet shows the line only with its date.
export const platformShape = z
  .object({
    databases: z.number().int().nonnegative(),
    servers: z.number().int().nonnegative(),
    timers: z.number().int().nonnegative(),
    as_of: z.iso.datetime({ offset: true }),
  })
  .nullable();
export const appsPayload = z.object({
  apps: appList,
  platform: platformShape,
  links: z.array(linkPreviewSchema).default([]),
});
export type AppsPayload = z.infer<typeof appsPayload>;
export function platformLine(shape: NonNullable<AppsPayload["platform"]>) {
  const count = (n: number, one: string, many: string) =>
    `${n} ${n === 1 ? one : many}`;
  return [
    count(shape.databases, "database", "databases"),
    count(shape.servers, "server", "servers"),
    `${shape.timers} on a timer`,
    `counted ${dayLabel(shape.as_of)}`,
  ].join(" · ");
}
export const appDescriptions = [
  {
    name: "Console",
    label: "Data platform",
    id: "music-data-platform",
    href: "/ops",
    tagline: "Holds the platform's music readings.",
    detail:
      "Python readers and SQL calculations run on configured servers. PostgreSQL keeps the numbers. Open Console for the data team's control panel.",
    marks: ["postgresql", "python", "dbt", "fly"],
  },
];
