import { session } from "../../server/session";
import { heartbeat } from "../../server/heartbeat";
import { proxyConsole } from "../../server/console-proxy";
import { readTimeoutResponse } from "../../server/read-timeout";
export const dynamic = "force-dynamic";
async function handle(request: Request) {
  try {
    const current = await session();
    if (!current)
      return new Response(null, {
        status: 303,
        headers: { Location: "/sign-in", "Cache-Control": "no-store" },
      });
    await heartbeat.refresh(current.person);
    return await proxyConsole(request, current);
  } catch (error) {
    const retry = readTimeoutResponse(request, error);
    if (retry) return retry;
    throw error;
  }
}
export {
  handle as GET,
  handle as POST,
  handle as PUT,
  handle as PATCH,
  handle as DELETE,
  handle as HEAD,
  handle as OPTIONS,
};
