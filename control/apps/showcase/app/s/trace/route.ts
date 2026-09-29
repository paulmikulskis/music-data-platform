import { session } from "../../../server/session";
import { traceData } from "../../../server/trace";
import { traceSelection } from "../../../lib/trace";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const current = await session();
  if (!current) return new Response("Open /sign-in.", { status: 401 });
  const parsed = traceSelection.safeParse(
    Object.fromEntries(new URL(request.url).searchParams),
  );
  if (!parsed.success)
    return Response.json(
      { message: "No path for that link. Pick a source below." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  try {
    return Response.json(await traceData(current.person, parsed.data), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json(
      { message: "Saved details are unavailable. Retry, or pick a source." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
