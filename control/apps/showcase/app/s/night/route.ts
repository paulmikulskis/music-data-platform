import { nightWindow } from "@mdp/contracts/platform";
import { session } from "../../../server/session";
import { night } from "../../../server/night";
import { errorStatus } from "../../../server/unknown";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const current = await session();
  if (!current) return new Response("Open /sign-in.", { status: 401 });
  const query = new URL(request.url).searchParams;
  const input = nightWindow.safeParse({
    since: query.get("since"),
    until: query.get("until"),
  });
  if (!input.success)
    return new Response("Choose a night again. Reload Home.", { status: 400 });
  try {
    return Response.json(await night(current.person, input.data), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    const status = errorStatus(error);
    return Response.json(
      { message: "The logbook is unavailable. Retry shortly." },
      {
        status:
          status === 401 || status === 403
            ? status
            : status === 429
              ? 429
              : 503,
        headers: { "Cache-Control": "no-store", "Retry-After": "5" },
      },
    );
  }
}
