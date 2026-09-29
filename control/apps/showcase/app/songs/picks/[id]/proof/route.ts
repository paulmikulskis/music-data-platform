import { origin } from "@mdp/showcase-auth";
import { session } from "../../../../../server/session";
import { verifyCallPlace } from "../../../../../server/call-proof";
import { resolveProof, type ProofResult } from "../../../../../server/proof";
import { signProof } from "../../../../../server/proof-token";
import { controlStore } from "../../../../../server/clients";
import { budget } from "../../../../../server/read-budget";
import { z } from "zod";
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const current = await session();
  if (!current) return Response.redirect(new URL("/sign-in", origin()), 303);
  const id = z.uuid().safeParse((await params).id);
  if (!id.success)
    return new Response("Pick unavailable. Open /songs?view=picks.", { status: 404 });
  const [stored] = await budget.run(
    "light",
    () =>
      controlStore()`SELECT song_key FROM control.showcase_call WHERE id=${id.data}`,
  );
  if (!stored)
    return new Response("Pick unavailable. Open /songs?view=picks.", { status: 404 });
  const mark = verifyCallPlace(
    new URL(request.url).searchParams.get("token"),
    id.data,
    current.handle,
  );
  const proof: ProofResult = mark
    ? await resolveProof(current.person, mark).catch(() => ({
        level: "unavailable",
        href: "/ops",
      }))
    : { level: "unavailable", href: "/ops" };
  const target = new URL(
    proof.level === "unavailable"
      ? `/s/song/${encodeURIComponent(z.string().parse(stored.song_key))}`
      : proof.href,
    origin(),
  );
  target.searchParams.set(
    "showcase_proof",
    signProof({
      level: proof.level,
      song: z.string().parse(stored.song_key),
      handle: current.handle,
    }),
  );
  return Response.redirect(target, 303);
}
