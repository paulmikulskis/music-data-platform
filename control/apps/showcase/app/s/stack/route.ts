import { session } from "../../../server/session";
import { stackStatus } from "../../../server/stack-status";
import { sensitiveStrings } from "../../../lib/stack";
export const dynamic = "force-dynamic";
export async function GET() {
  const current = await session();
  if (!current) return new Response("Open /sign-in.", { status: 401 });
  try {
    const status = await stackStatus(current.person);
    // A probe note never carries an address, host or identifier to the browser.
    if (sensitiveStrings(status.services).length)
      return new Response("Status unavailable. Retry.", { status: 503 });
    return Response.json(status, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return new Response("Status unavailable. Retry.", {
      status: 503,
      headers: { "Cache-Control": "no-store", "Retry-After": "5" },
    });
  }
}
