import { z } from "zod";
import { session } from "../../../../server/session";
import { signProof } from "../../../../server/proof-token";
export async function GET(request: Request) {
  const current = await session();
  if (!current)
    return new Response(null, {
      status: 303,
      headers: { Location: "/sign-in" },
    });
  const query = new URL(request.url).searchParams;
  const id = z.uuid().safeParse(query.get("run"));
  const attempt = z.coerce
    .number()
    .int()
    .nonnegative()
    .safeParse(query.get("attempt"));
  if (!id.success || !attempt.success)
    return new Response("Reading unavailable. Open Home.", { status: 400 });
  const token = signProof({
    level: "source",
    handle: current.handle,
    song: "",
    back: `/?sheet=last-night&moment=${id.data}:${attempt.data}`,
  });
  return new Response(null, {
    status: 303,
    headers: {
      Location: `/runs/${id.data}?showcase_proof=${encodeURIComponent(token)}`,
      "Cache-Control": "no-store",
    },
  });
}
