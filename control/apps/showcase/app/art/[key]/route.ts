import { session } from "../../../server/session";
import { identities } from "../../../server/reads";
import { coverFor, cachedArtColor } from "../../../server/art";
export const dynamic = "force-dynamic";
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ key: string }> },
) {
  if (!(await session()))
    return new Response("Open /sign-in.", { status: 401 });
  const { key } = await params;
  if (new URL(_request.url).searchParams.get("palette") === "1") {
    return Response.json(
      { color: cachedArtColor(key) },
      { headers: { "Cache-Control": "private, no-store" } },
    );
  }
  const cover = await coverFor(
    key,
    async () => (await identities(key)).value.rows,
  );
  if (!cover)
    return new Response("Artwork unavailable. Open /songs.", { status: 404 });
  return new Response(Buffer.from(cover.bytes, "base64"), {
    headers: {
      "Content-Type": cover.type,
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
