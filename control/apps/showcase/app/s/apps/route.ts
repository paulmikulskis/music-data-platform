import publicHostValues from "../../../lib/public-hosts.json";
const publicHosts: { name: string; host: string; health: string }[] = publicHostValues;
import { required } from "@mdp/showcase-auth";
import { session } from "../../../server/session";
import { budget } from "../../../server/read-budget";
import { stackView } from "../../../server/stack";
import { readLinks } from "../../../server/links";
const appCache = budget.cache<{
  state: "live" | "unknown";
  checked_at: string;
}>();
export async function GET() {
  if (!(await session()))
    return new Response("Open /sign-in.", { status: 401 });
  const entries = [
    {
      name: "Console",
      href: "/ops",
      health: new URL("/health", required("MDP_CONTROL_API_URL")).toString(),
    },
    ...publicHosts.map((entry) => ({
      name: entry.name,
      href: entry.host,
      health: entry.health,
    })),
  ];
  const apps = await Promise.all(
    entries.map(async (app) => {
      const result = await appCache
        .read(`app:${app.name}`, "light", async () => {
          const response = await fetch(app.health, {
            redirect: "manual",
            cache: "no-store",
            signal: AbortSignal.timeout(1800),
          });
          await response.body?.cancel();
          return {
            state: response.ok ? ("live" as const) : ("unknown" as const),
            checked_at: new Date().toISOString(),
          };
        })
        .catch(() => null);
      return {
        name: app.name,
        href: app.href,
        state: result?.state === "live" ? result.value.state : "unknown",
        checked_at: result?.value.checked_at ?? null,
      };
    }),
  );
  const reader = readLinks();
  const links = [
    "console",
  ].flatMap((id) => {
    const preview = reader.get(`avatar-open-${id}`);
    return preview ? [preview] : [];
  });
  return Response.json(
    { apps, platform: stackView().shape, links },
    { headers: { "Cache-Control": "no-store" } },
  );
}
