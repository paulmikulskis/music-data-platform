import { z } from "zod";
import { origin } from "@mdp/showcase-auth";
import { session } from "../../../../../../server/session";
import {
  resolveCycleProof,
  type ProofResult,
} from "../../../../../../server/proof";
import { signProof } from "../../../../../../server/proof-token";
import { viewerPath } from "../../../../../../lib/proof-link";

// The operator console behind a number's proof summary. Its Back bar returns to that summary.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ cycle: string }> },
) {
  const current = await session();
  if (!current) return Response.redirect(new URL("/sign-in", origin()), 303);
  const query = new URL(request.url).searchParams;
  const cycle = z.uuid().safeParse((await params).cycle);
  const relation = z
    .string()
    .regex(/^(marts\.)?mart_[a-z0-9_]+$/)
    .safeParse(query.get("relation"));
  const proof: ProofResult = cycle.success
    ? await resolveCycleProof(
        current.person,
        cycle.data,
        relation.success ? relation.data.replace(/^marts\./, "") : "",
      ).catch((): ProofResult => ({ level: "unavailable", href: "/ops" }))
    : { level: "unavailable", href: "/ops" };
  const back = viewerPath(query.get("back")) ?? "/songs?view=rising";
  const target = new URL(
    proof.level === "unavailable" ? back : proof.href,
    origin(),
  );
  target.searchParams.set(
    "showcase_proof",
    signProof({ level: proof.level, song: "", handle: current.handle, back }),
  );
  return Response.redirect(target, 303);
}
